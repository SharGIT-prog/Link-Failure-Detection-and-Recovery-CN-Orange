# topology.py
"""
Mininet Topology for SDN Controller Testing
Simple linear topology
"""

import sys
import time
import logging
from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.cli import CLI
from mininet.link import TCLink
from mininet.log import setLogLevel

LOG = logging.getLogger(__name__)


def create_simple_topology():
    """
    Create a simple topology for basic testing.
    """
    net = Mininet(
        controller=RemoteController,
        switch=OVSSwitch,
        link=TCLink
    )

    print("=" * 60)
    print("*** Setting up SIMPLE LINEAR TOPOLOGY ***")
    print("=" * 60)

    print("\n[1] Adding controller")
    c0 = net.addController(
        'c0',
        ip='127.0.0.1',
        port=6653
    )

    print("[2] Adding switches (OpenFlow 1.3)")
    s1 = net.addSwitch('s1', protocols='OpenFlow13')
    s2 = net.addSwitch('s2', protocols='OpenFlow13')

    print("[3] Adding hosts")
    h1 = net.addHost('h1', ip='10.0.0.1')
    h2 = net.addHost('h2', ip='10.0.0.2')

    print("[4] Creating links")
    # Hosts to switches
    net.addLink(h1, s1)
    net.addLink(h2, s2)

    # Switch to switch (simple linear)
    net.addLink(s1, s2)

    print("\n[5] Building and starting network")
    net.build()
    c0.start()
    s1.start([c0])
    s2.start([c0])

    # Wait for controller to configure switches
    time.sleep(3)

    print("\n" + "=" * 60)
    print("*** TOPOLOGY CREATED SUCCESSFULLY ***")
    print("=" * 60)
    print("\nTopology Map:")
    print("  h1 (10.0.0.1) --- s1 --- s2 --- h2 (10.0.0.2)")
    print("\nController: 127.0.0.1:6653 (Ryu)")
    print("\nNow entering Mininet CLI...")
    print("=" * 60 + "\n")

    return net


def create_triangle_topology():
    """
    Create a more complex topology with redundancy.
    """
    net = Mininet(
        controller=RemoteController,
        switch=OVSSwitch,
        link=TCLink
    )

    print("=" * 60)
    print("*** Setting up TRIANGLE TOPOLOGY (with redundancy) ***")
    print("=" * 60)

    print("\n[1] Adding controller")
    c0 = net.addController(
        'c0',
        ip='127.0.0.1',
        port=6653
    )

    print("[2] Adding switches (OpenFlow 1.3)")
    s1 = net.addSwitch('s1', protocols='OpenFlow13')
    s2 = net.addSwitch('s2', protocols='OpenFlow13')
    s3 = net.addSwitch('s3', protocols='OpenFlow13')

    print("[3] Adding hosts")
    h1 = net.addHost('h1', ip='10.0.0.1')
    h2 = net.addHost('h2', ip='10.0.0.2')

    print("[4] Creating links")
    # Hosts to switches
    net.addLink(h1, s1)
    net.addLink(h2, s3)

    # Switch interconnection
    net.addLink(s1, s2)
    net.addLink(s2, s3)
    net.addLink(s1, s3)

    print("\n[5] Building and starting network")
    net.build()
    c0.start()
    s1.start([c0])
    s2.start([c0])
    s3.start([c0])

    # Wait for controller to configure switches
    time.sleep(3)

    print("\n" + "=" * 60)
    print("*** TOPOLOGY CREATED SUCCESSFULLY ***")
    print("=" * 60)
    print("\nController: 127.0.0.1:6653 (Ryu)")
    print("\nNow entering Mininet CLI...")
    print("=" * 60 + "\n")

    return net


def run():
    """Main function to start topology and CLI."""

    # Choose which topology to use
    print("\n[SELECT TOPOLOGY]")
    print("1. Simple Linear ")
    print("2. Triangle (with redundancy) - For link failure testing")

    choice = input("\nEnter choice (1 or 2) [default: 1]: ").strip()

    if choice == '2':
        net = create_triangle_topology()
    else:
        net = create_simple_topology()

    try:
        CLI(net)
    finally:
        print("\n\n*** Stopping network...")
        net.stop()
        print("*** Network stopped")


if __name__ == '__main__':
    # Use 'output' level to show clean CLI output without verbose logs
    setLogLevel('output')

    run()
