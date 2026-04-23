# controller.py
"""
SDN Controller:
- Topology Monitoring (detect topology changes)
- Link Failure Detection (detect link failures)
- Dynamic Routing (update flows based on topology)
- Connectivity Restoration (restore connectivity after link failures)
"""

import logging
import sys
from collections import defaultdict
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet
from ryu.topology import event as topo_event

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(message)s', stream=sys.stdout)
LOG = logging.getLogger(__name__)
LOG.setLevel(logging.INFO)

# Suppress verbose Ryu loggers
for logger_name in ['ryu.base.app_manager', 'ryu.controller.controller',
                     'ryu.ofproto.ofproto_parser', 'ryu.topology', 'mininet']:
    logging.getLogger(logger_name).setLevel(logging.ERROR)


class SDNController(app_manager.RyuApp):

    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(SDNController, self).__init__(*args, **kwargs)
        self.mac_to_port = {}                    # dpid -> {mac: port}
        self.active_links = set()                # (src_dpid,
src_port, dst_dpid, dst_port)
        self.down_links = set()                  # Failed links
        self.datapaths = {}                      # dpid -> datapath object
        self.failed_ports = defaultdict(list)    # dpid -> [failed ports]

        LOG.info('[INIT] SDN Controller initialized')

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        """Install table-miss flow entry to send unmatched packets to
controller."""
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        dpid = datapath.id

        self.datapaths[dpid] = datapath
        self.mac_to_port.setdefault(dpid, {})
        self.failed_ports.setdefault(dpid, [])

        # Install table-miss flow entry
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, match, actions, idle_timeout=0,
hard_timeout=0)

        LOG.info('[SWITCH_CONNECT] Switch DPID=%s connected', hex(dpid))

    def add_flow(self, datapath, priority, match, actions,
idle_timeout=15, hard_timeout=0):
        """Install a flow rule in the switch."""
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS,
actions)]
        mod = parser.OFPFlowMod(
            datapath=datapath,
            priority=priority,
            match=match,
            instructions=inst,
            idle_timeout=idle_timeout,
            hard_timeout=hard_timeout,
            command=ofproto.OFPFC_ADD
        )
        datapath.send_msg(mod)
        LOG.debug('[FLOW_INSTALL] DPID=%s Priority=%d',
hex(datapath.id), priority)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        """Handle packet arrival: MAC learning and forwarding."""
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        dpid = datapath.id
        in_port = msg.match['in_port']

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)

        if eth is None or eth.ethertype == 35020:  # Ignore LLDP
            return

        dst = eth.dst
        src = eth.src

        # MAC Learning
        if src not in self.mac_to_port[dpid]:
            self.mac_to_port[dpid][src] = in_port
            LOG.info('[MAC_LEARN] DPID=%s MAC %s on port %d',
hex(dpid), src, in_port)

        # Routing Decision
        if dst == 'ff:ff:ff:ff:ff:ff':  # Broadcast
            out_port = ofproto.OFPP_FLOOD
        elif dst in self.mac_to_port[dpid]:
            preferred_port = self.mac_to_port[dpid][dst]
            if preferred_port not in self.failed_ports[dpid]:
                out_port = preferred_port
            else:
                # Try alternate path using multi-hop routing
                out_port = self.find_alternate_port(dst, dpid, ofproto)
        else:
            out_port = self.find_alternate_port(dst, dpid, ofproto)

        # Install Flow Rule
        actions = [parser.OFPActionOutput(out_port)]
        if out_port != ofproto.OFPP_FLOOD and out_port != in_port:
            match = parser.OFPMatch(in_port=in_port, eth_dst=dst, eth_src=src)
            self.add_flow(datapath, 10, match, actions,
idle_timeout=15, hard_timeout=0)

        # Send Packet Out
        out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=msg.buffer_id,
            in_port=in_port,
            actions=actions,
            data=msg.data if msg.buffer_id == ofproto.OFP_NO_BUFFER else None
        )
        datapath.send_msg(out)

    def find_alternate_port(self, dst_mac, current_dpid, ofproto):
        """Find alternate port to reach destination using shortest path."""
        for dpid, ports in self.mac_to_port.items():
            if dst_mac in ports:
                path = self.dijkstra_shortest_path(current_dpid, dpid)
                if path and len(path) > 1:
                    out_port = path[0][1]
                    if out_port is not None:
                        LOG.info('[ROUTING] Found alternate path via
port %d', out_port)
                        return out_port

        LOG.debug('[ROUTING] No path found, flooding')
        return ofproto.OFPP_FLOOD

    def build_topology_graph(self):
        """Build adjacency graph from active links."""
        graph = defaultdict(list)
        for src_dpid, src_port, dst_dpid, dst_port in self.active_links:
            graph[src_dpid].append((dst_dpid, src_port, dst_port))
            graph[dst_dpid].append((src_dpid, dst_port, src_port))

        LOG.debug('[TOPOLOGY] Graph: %d switches, %d links',
len(graph), len(self.active_links))
        return graph

    def dijkstra_shortest_path(self, start_dpid, end_dpid):
        """Find shortest path using Dijkstra's algorithm."""
        if start_dpid == end_dpid:
            return [(start_dpid, None)]

        graph = self.build_topology_graph()
        distances = {dpid: float('inf') for dpid in graph}
        distances[start_dpid] = 0
        previous = {dpid: None for dpid in graph}
        unvisited = set(graph.keys())

        while unvisited:
            current = min(unvisited, key=lambda x: distances[x])
            if distances[current] == float('inf'):
                break
            if current == end_dpid:
                break
            unvisited.remove(current)

            for neighbor, outport, inport in graph[current]:
                if neighbor in unvisited:
                    new_dist = distances[current] + 1
                    if new_dist < distances[neighbor]:
                        distances[neighbor] = new_dist
                        previous[neighbor] = (current, outport)

        if distances[end_dpid] == float('inf'):
            LOG.warning('[ROUTING] No path from %s to %s',
hex(start_dpid), hex(end_dpid))
            return None

        path = []
        current = end_dpid
        while previous[current] is not None:
            prev_dpid, outport = previous[current]
            path.insert(0, (prev_dpid, outport))
            current = prev_dpid
        path.append((end_dpid, None))

        LOG.info('[ROUTING] Path found: %d hops', len(path))
        return path


    @set_ev_cls(topo_event.EventSwitchEnter)
    def switch_enter_handler(self, ev):
        """Handle switch entering network."""
        LOG.info('[TOPOLOGY] Switch %s entered', hex(ev.switch.dp.id))

    @set_ev_cls(topo_event.EventSwitchLeave)
    def switch_leave_handler(self, ev):
        """Handle switch leaving network."""
        LOG.warning('[TOPOLOGY] Switch %s left', hex(ev.switch.dp.id))

    @set_ev_cls(topo_event.EventLinkAdd)
    def link_add_handler(self, ev):
        """Handle link discovery or restoration."""
        link = ev.link
        link_key = (link.src.dpid, link.src.port_no, link.dst.dpid,
link.dst.port_no)

        self.active_links.add(link_key)

        if link_key in self.down_links:
            self.down_links.remove(link_key)
            LOG.critical('[LINK_RESTORED] Link %s->%s is UP',
hex(link.src.dpid), hex(link.dst.dpid))
            self.on_link_restored(link.src.dpid, link.src.port_no,
link.dst.dpid, link.dst.port_no)
        else:
            LOG.info('[LINK_DISCOVERED] Link %s->%s discovered',
hex(link.src.dpid), hex(link.dst.dpid))

    @set_ev_cls(topo_event.EventLinkDelete)
    def link_delete_handler(self, ev):
        """Handle link failure - MAIN DETECTION EVENT."""
        link = ev.link
        src_dpid = link.src.dpid
        src_port = link.src.port_no
        dst_dpid = link.dst.dpid
        dst_port = link.dst.port_no

        link_key = (src_dpid, src_port, dst_dpid, dst_port)

        self.active_links.discard(link_key)
        self.down_links.add(link_key)

        if src_port not in self.failed_ports[src_dpid]:
            self.failed_ports[src_dpid].append(src_port)
        if dst_port not in self.failed_ports[dst_dpid]:
            self.failed_ports[dst_dpid].append(dst_port)

        LOG.critical('[LINK_FAILURE] *** LINK DOWN ***')
        LOG.critical('[LINK_DOWN] %s(port %d) <-> %s(port %d)',
                    hex(src_dpid), src_port, hex(dst_dpid), dst_port)

        self.initiate_recovery(src_dpid, src_port, dst_dpid, dst_port)

    def initiate_recovery(self, src_dpid, src_port, dst_dpid, dst_port):
        """Initiate recovery when link fails."""
        LOG.critical('[RECOVERY_START] Starting link recovery procedure')

        # Clear flow rules ONLY - keep MAC tables intact!
        # Reason: If MACs are cleared on the switch with hosts (e.g.,
s3 has h2),
        # the destination location will be lost, and routing via
alternate paths cannot take place.

        if src_dpid in self.datapaths:
            self.clear_flow_rules(self.datapaths[src_dpid], src_dpid)
            LOG.warning('[RECOVERY_FLOWS] Cleared flows on DPID=%s
(link side 1)', hex(src_dpid))

        if dst_dpid in self.datapaths:
            self.clear_flow_rules(self.datapaths[dst_dpid], dst_dpid)
            LOG.warning('[RECOVERY_FLOWS] Cleared flows on DPID=%s
(link side 2)', hex(dst_dpid))

        LOG.critical('[RECOVERY_COMPLETE] Recovery initiated - next
packets will use alternate paths')

    def clear_mac_entries(self, dpid):
        """Clear MAC entries to force re-learning."""
        if dpid in self.mac_to_port:
            count = len(self.mac_to_port[dpid])
            self.mac_to_port[dpid].clear()
            LOG.warning('[CLEAR_MACS] Cleared %d MAC entries on
DPID=%s', count, hex(dpid))

    def clear_flow_rules(self, datapath, dpid):
        """Clear flow rules to enable rerouting (keep table-miss)."""
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        try:
            match = parser.OFPMatch()
            mod = parser.OFPFlowMod(
                datapath=datapath,
                command=ofproto.OFPFC_DELETE,
                priority=10,
                out_port=ofproto.OFPP_ANY,
                out_group=ofproto.OFPG_ANY,
                match=match
            )
            datapath.send_msg(mod)
            LOG.warning('[CLEAR_FLOWS] Cleared flows on DPID=%s', hex(dpid))
        except Exception as e:
            LOG.error('[CLEAR_FLOWS_ERROR] %s', str(e))

    def on_link_restored(self, src_dpid, src_port, dst_dpid, dst_port):
        """Handle link restoration - clear failed port markers."""
        try:
            if src_port in self.failed_ports[src_dpid]:
                self.failed_ports[src_dpid].remove(src_port)
            if dst_port in self.failed_ports[dst_dpid]:
                self.failed_ports[dst_dpid].remove(dst_port)
            LOG.warning('[PORTS_AVAILABLE] Ports are now available for routing')
        except Exception as e:
            LOG.error('[RESTORE_ERROR] %s', str(e))
