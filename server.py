# server.py
from flask import Flask, jsonify, request, Response, stream_with_context
import threading
from queue import SimpleQueue
import os
import json
import traceback
from pathlib import Path
import time
import sys
import yaml
import re
import glob
import shutil

# Reuse your helpers
from file_utils import save_output_to_file, del_partial_logs
from serial_utils import (
    connect_to_serial,
    READ_TIMEOUT,
    disable_paging,
    send_command,
    enter_enable_mode,
    logout_close_connection,
    get_hostname,
    wait_for_prompt,
)
from remote_utils import (
    remote_connect,
    disable_paging_remote,
    enter_enable_mode_remote,
    send_command_remote,
    get_hostname_remote,
)
from command_manager import load_commands, save_commands
from comparsion_engine.parser import parse_device_logs, normalize_parsed_config
from comparsion_engine.comparator import compare_dicts
from comparsion_engine.student_manager import find_show_run_file

app = Flask(__name__)

# Base directory for consistent absolute paths
BASE_DIR = Path(__file__).resolve().parent

# Grading Directories
SCHEMES_DIR = BASE_DIR / "schemes"
RUBRICS_DIR = BASE_DIR / "rubrics"
TEMPLATES_DIR = BASE_DIR / "comparsion_engine" / "templates"
ENGINE_STUDENTS_DIR = BASE_DIR / "comparsion_engine" / "students"
# Results are stored under Documents/<Exam>/<Session>/<Student>/results
# Results are stored under Documents/<Exam>/<Session>/<Student>/results
RESULTS_DIR = None
GRADING_POLICY_PATH = BASE_DIR / "config" / "grading_policy.json"
RUBRIC_RULES_PATH = BASE_DIR / "config" / "rubric_rules.json"
DOCS_DIR = (Path.home() / "Documents").resolve()
SCHEMES_DIR.mkdir(exist_ok=True)
RUBRICS_DIR.mkdir(exist_ok=True)
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
ENGINE_STUDENTS_DIR.mkdir(parents=True, exist_ok=True)

connection_lock = threading.Lock()

current_mode = None  # "serial" or "ssh"
serial_conn = None
serial_hostname = None
last_used_serial_settings = {"port": "/dev/ttyUSB0", "baudrate": 9600}

ssh_client = None
ssh_hostname = None
last_used_ssh_credentials = {"host": None, "username": None, "password": None, "port": 22}


def _close_serial_connection():
    global serial_conn, serial_hostname
    ser = None
    with connection_lock:
        if serial_conn:
            ser = serial_conn
            serial_conn = None
            serial_hostname = None
    if ser:
        try:
            logout_close_connection(ser)
        except Exception:
            pass


def _close_ssh_connection():
    global ssh_client, ssh_hostname
    client = None
    with connection_lock:
        if ssh_client:
            client = ssh_client
            ssh_client = None
            ssh_hostname = None
    if client:
        try:
            shell = getattr(client, "_shell", None)
            if shell:
                shell.close()
        except Exception:
            pass
        try:
            client.close()
        except Exception:
            pass


def _is_ssh_client_active(client):
    if not client:
        return False
    try:
        transport = client.get_transport()
        return transport and transport.is_active()
    except Exception:
        return False


def _update_serial_state(ser, port, baudrate, hostname):
    global serial_conn, serial_hostname, current_mode
    with connection_lock:
        last_used_serial_settings["port"] = port
        last_used_serial_settings["baudrate"] = baudrate
        serial_conn = ser
        serial_hostname = hostname
        current_mode = "serial"


def _update_ssh_state(client, host, username, password, hostname, port):
    global ssh_client, ssh_hostname, current_mode
    with connection_lock:
        last_used_ssh_credentials["host"] = host
        last_used_ssh_credentials["username"] = username
        last_used_ssh_credentials["password"] = password
        last_used_ssh_credentials["port"] = port
        ssh_client = client
        ssh_hostname = hostname
        current_mode = "ssh"


def _default_grading_policy():
    return {
        "major_threshold": 1,
        "minor_threshold": 5,
    }


def _default_rubric_rules():
    # ACCMS outcome list (rubric-aligned). Patterns can be edited in System Admin.
    accms_outcomes = [
        ("MISMATCH_HOSTNAME", "minor", "Device hostname differs from template."),
        ("MISMATCH_MOTD", "minor", "MOTD banner content differs from template."),
        ("EXTRA_MOTD", "minor", "MOTD configured when it should not be."),
        ("MISMATCH_ROUTING_PROTOCOL", "major", "Wrong routing protocol type configured."),
        ("MISMATCH_IRP_INCONSEQUENTIAL", "minor", "Non-functioning routing protocol configured."),
        ("MISMATCH_ROUTING_NETWORK", "major", "Routing protocol network statement differs."),
        ("EXTRA_NETWORK_ADVERTISED", "minor", "Network advertised when it should not be."),
        ("MISMATCH_AUTO_SUMMARY", "minor", "Auto-summarisation not disabled when required."),
        ("MISMATCH_AUTO_SUMMARY_INCONSEQUENTIAL", "minor", "Auto-summarisation not disabled (inconsequential)."),
        ("EXTRA_REDISTRIBUTE", "minor", "Default routes redistributed when they should not be."),
        ("MISSING_REDISTRIBUTE", "major", "Default routes not redistributed when required."),
        ("MISSING_NETWORK_STATEMENT_INTERFACE", "major", "Routing protocol network statement matches no interface."),
        ("EXTRA_ROUTING_INSTANCE", "major", "Multiple routing protocol instances configured."),
        ("MISMATCH_RIP_VERSION", "minor", "RIP version incorrectly set."),
        ("MISMATCH_EIGRP_AS_MISMATCH", "major", "EIGRP AS numbers not common across devices."),
        ("MISMATCH_EIGRP_AS_INCORRECT", "major", "EIGRP AS number incorrectly set."),
        ("MISMATCH_ROUTING_PASSIVE", "minor", "Passive interface declaration differs."),
        ("MISMATCH_ROUTING_REDISTRIBUTE", "minor", "Redistribution statement differs."),
        ("MISMATCH_ROUTING_AUTO_SUMMARY", "minor", "Auto-summary setting differs."),
        ("MISMATCH_OSPF_AREA_ID_SINGLE", "major", "Single-area OSPF not using area 0."),
        ("MISMATCH_OSPF_AREA_0_MISSING", "major", "Multi-area OSPF missing area 0."),
        ("MISMATCH_OSPF_AREA_LINK", "major", "Interfaces on same link in different OSPF areas."),
        ("MISMATCH_OSPF_NOT_MULTIAREA", "major", "OSPF is not multi-area when required."),
        ("MISMATCH_OSPF_NOT_SINGLEAREA", "major", "OSPF is not single-area when required."),
        ("MISMATCH_OSPF_GROUP_MULTIAREA", "major", "OSPF network groups not in a single area."),
        ("MISMATCH_OSPF_WRONG_AREA", "major", "OSPF network groups in wrong area."),
        ("MISSING_OSPF_POINT_TO_POINT", "minor", "Missing ip ospf network point-to-point command."),
        ("MISMATCH_STATIC_ROUTE_NETWORK", "major", "Static route destination differs."),
        ("MISMATCH_STATIC_ROUTE_NEXTHOP", "minor", "Static route next-hop differs."),
        ("MISMATCH_DEFAULT_ROUTE_NEXTHOP", "major", "Default route next-hop differs."),
        ("EXTRA_STATIC_ROUTE", "minor", "Extra static route added when not required."),
        ("EXTRA_DEFAULT_ROUTE", "minor", "Multiple correct default routes configured."),
        ("EXTRA_STATIC_CONFIGURED", "minor", "Static routes installed when there should be none."),
        ("MISMATCH_DHCP_POOL", "major", "DHCP pool properties incorrect."),
        ("MISMATCH_DHCP_EXCLUDED", "minor", "DHCP excluded range differs."),
        ("MISMATCH_DHCP_BAD_EXCLUSION", "minor", "Excluded IPs outside pool range."),
        ("EXTRA_DHCP_POOL", "major", "DHCP configured when it should not be."),
        ("EXTRA_DHCP_ADVERTISING", "minor", "DHCP pools advertising when should not be."),
        ("MISMATCH_NAT_DIRECTION", "major", "NAT inside/outside assignment differs."),
        ("MISMATCH_NAT_POOL", "major", "NAT pool range differs."),
        ("MISMATCH_NAT_POOL_RANGE_OUTSIDE", "major", "NAT pool range extends outside required range."),
        ("MISMATCH_NAT_POOL_RANGE_INSIDE", "major", "NAT pool range does not cover required range."),
        ("MISMATCH_NAT_POOL_PREFIX", "major", "NAT pool prefix incorrect."),
        ("MISMATCH_NAT_ACL_BINDING", "major", "NAT ACL to pool binding differs."),
        ("MISMATCH_NAT_ACL_INCORRECT", "major", "NAT ACL does not match required IPs."),
        ("MISMATCH_NAT_ACL_NOT_OPTIMAL", "minor", "NAT ACL not using optimal set."),
        ("MISMATCH_NAT_ACL_POINTLESS_NORMAL", "minor", "Pointless NAT ACL rules shadowed by earlier rules."),
        ("MISMATCH_NAT_ACL_POINTLESS_DEFAULT", "minor", "Pointless NAT ACL rules after permit/deny any any."),
        ("EXTRA_NAT_OVERLOAD", "major", "NAT pool overloaded when it should not be."),
        ("EXTRA_NAT_INTERFACE_CONFIGURED", "major", "NAT configured on interface when it should not be."),
        ("MISMATCH_IP", "major", "Interface IP address differs from template."),
        ("MISMATCH_MASK", "major", "Interface subnet mask differs."),
        ("MISMATCH_GATEWAY_ADDRESS", "major", "Incorrect IP on gateway interface."),
        ("MISMATCH_HOST_ADDRESS", "minor", "Incorrect IP on host interface."),
        ("MISMATCH_TRUNK_IP_ASSIGNED", "major", "Trunk interface has IP when it should have sub-interfaces."),
        ("MISMATCH_SUB_INT_VLAN", "major", "Sub-interface name does not match VLAN ID."),
        ("MISMATCH_LINK_NETWORK", "major", "Shared L3 link not in same network."),
        ("MISMATCH_LINK_CONFLICT_ADDRESS", "major", "Shared L3 link has conflicting IPs."),
        ("MISMATCH_SHUTDOWN", "major", "Interface shutdown state differs."),
        ("MISMATCH_CONFIGURED_IF_SHUTDOWN", "minor", "Interface shutdown but configured."),
        ("MISMATCH_CLOCK_RATE", "minor", "Serial clock rate differs."),
        ("MISSING_DESCRIPTION", "minor", "Required interface description not set."),
        ("EXTRA_DESCRIPTION", "minor", "Interface description set when it should not be."),
        ("EXTRA_IFACE_CONFIGURED", "minor", "Interface configured when it should not have been."),
        ("MISMATCH_ENCAPSULATION", "major", "Serial encapsulation type differs."),
        ("EXTRA_PPP_ENABLED", "major", "PPP enabled when it should not be."),
        ("MISMATCH_PPP_AUTH", "major", "PPP authentication method differs."),
        ("MISMATCH_PPP_PROTOCOL_CONFLICT", "major", "PPP protocol conflict on shared link."),
        ("MISMATCH_PPP_USERNAME", "major", "Incorrect PPP username."),
        ("MISMATCH_PPP_PASSWORD", "major", "Incorrect PPP password."),
        ("MISSING_PPP_ACCOUNT", "major", "Required PPP account missing."),
        ("MISMATCH_PPP_ACCOUNT_PASSWORD", "major", "PPP account has invalid password."),
        ("MISMATCH_ACL_TYPE", "major", "ACL type differs."),
        ("MISMATCH_ACL_RULE", "major", "ACL rule content differs."),
        ("MISMATCH_ACL_NOT_OPTIMAL", "minor", "ACL not using optimal rules."),
        ("MISMATCH_ACL_POINTLESS_NORMAL", "minor", "ACL rules shadowed by earlier rules."),
        ("MISMATCH_ACL_POINTLESS_DEFAULT", "minor", "ACL rules after permit/deny any any."),
        ("MISMATCH_ACL_UNCLEAN_PRUNE", "minor", "ACL rule ranges outside equivalent of any."),
        ("EXTRA_ACL_APPLIED", "major", "ACL applied to interface when it should not be."),
        ("MISSING_ACL_APPLIED", "major", "ACL configured but not applied."),
        ("MISMATCH_SWITCHPORT_MODE", "major", "Switchport mode differs."),
        ("MISMATCH_ACCESS_VLAN", "major", "Access VLAN number differs."),
        ("MISMATCH_VLAN_INTERFACE", "major", "VLAN SVI numbering differs from template."),
        ("MISMATCH_VLAN_ID", "major", "VLAN ID differs."),
        ("MISMATCH_VLAN_NAME", "minor", "VLAN name differs."),
        ("MISMATCH_TRUNK_ENCAPSULATION", "major", "Trunk encapsulation differs."),
        ("MISMATCH_TRUNK_MODE", "major", "Trunk mode differs."),
        ("MISMATCH_TRUNK_NATIVE_VLAN", "major", "Trunk native VLAN differs."),
        ("MISMATCH_TRUNK_ALLOWED_VLANS", "major", "Allowed VLAN list differs."),
        ("MISMATCH_TRUNK_BOTH_ENDS", "major", "Trunk not trunking at both ends."),
        ("MISMATCH_TRUNK_ENCAP_MISMATCH", "major", "Trunk encapsulation mismatch."),
        ("MISMATCH_TRUNK_NATIVE_VLAN_MISMATCH", "major", "Native VLAN mismatch between switches."),
        ("MISMATCH_PORT_SECURITY_MAX", "minor", "Port security maximum differs."),
        ("MISMATCH_PORT_SECURITY_VIOLATION", "major", "Port security violation action differs."),
        ("MISMATCH_PORT_SECURITY_STICKY", "minor", "Port security sticky setting differs."),
        ("MISMATCH_STP_PROTOCOL", "major", "Wrong spanning tree protocol applied."),
        ("MISMATCH_STP_MODE", "minor", "Spanning tree mode differs."),
        ("MISMATCH_STP_PRIORITY", "major", "Bridge priority differs."),
        ("MISMATCH_STP_PORT_COST", "minor", "STP port cost differs."),
        ("MISMATCH_STP_PORT_PRIORITY", "minor", "STP port priority differs."),
        ("MISMATCH_STP_ROOT_GUARD", "minor", "Root guard state differs."),
        ("MISMATCH_STP_BPDU_GUARD", "minor", "BPDU guard state differs."),
        ("MISMATCH_STP_PORTFAST", "minor", "PortFast state differs."),
        ("MISMATCH_ETHERCHANNEL_GROUP", "major", "Port-channel group number differs."),
        ("MISMATCH_ETHERCHANNEL_MODE", "major", "EtherChannel mode differs."),
        ("MISMATCH_ETHERCHANNEL_MEMBERS", "major", "Port-channel members differ."),
        ("MISMATCH_ETHERCHANNEL_LOAD_BALANCE", "minor", "EtherChannel load-balancing differs."),
        ("MISMATCH_PORTCHANNEL_VLAN", "major", "VLANs allowed on port-channel trunk differ."),
        ("MISMATCH_PORTCHANNEL_TRUNK_MODE", "major", "Port-channel trunk mode differs."),
        ("MISMATCH_PAGP_MODE", "major", "PAgP mode differs."),
        ("MISMATCH_PAGP_LEARN_METHOD", "minor", "PAgP learn method differs."),
        ("MISMATCH_LACP_MODE", "major", "LACP mode differs."),
        ("MISMATCH_LACP_PORT_PRIORITY", "minor", "LACP port priority differs."),
        ("MISMATCH_LACP_SYSTEM_PRIORITY", "minor", "LACP system priority differs."),
        ("MISMATCH_VTY_TRANSPORT", "minor", "VTY transport input differs."),
        ("MISMATCH_LINE_PASSWORD", "major", "Incorrect password on VTY/console lines."),
        ("EXTRA_LINE_ENABLED", "major", "Line access enabled when it should not be."),
        ("MISMATCH_USER_PRIVILEGE", "major", "User privilege level differs."),
        ("MISMATCH_USER_AUTH_TYPE", "minor", "User authentication type differs."),
        ("MISMATCH_HTTP_SERVER", "minor", "HTTP server state differs."),
        ("MISSING_HOSTNAME", "minor", "Device hostname not configured."),
        ("MISSING_MOTD", "minor", "Required MOTD banner not configured."),
        ("MISSING_ROUTING_PROTOCOL", "major", "Routing protocol entirely absent."),
        ("MISSING_ROUTING_NETWORK", "major", "Network statement absent from routing protocol."),
        ("MISSING_STATIC_ROUTE", "major", "Static route absent."),
        ("MISSING_DEFAULT_ROUTE", "major", "Default route not configured."),
        ("MISSING_DEFAULT_GATEWAY", "major", "Switch default gateway not configured."),
        ("MISSING_PASSIVE_INTERFACE", "minor", "Passive interface missing."),
        ("MISSING_REDISTRIBUTE", "major", "Default routes not redistributed when required."),
        ("MISSING_DHCP_POOL", "major", "Required DHCP pool not configured."),
        ("MISSING_DHCP_EXCLUDED", "minor", "DHCP excluded range not configured."),
        ("MISSING_DHCP_EMPTY_POOL", "major", "DHCP pool created but no addresses."),
        ("MISSING_NAT_INSIDE", "major", "NAT inside not declared on interface."),
        ("MISSING_NAT_OUTSIDE", "major", "NAT outside not declared on interface."),
        ("MISSING_NAT_POOL", "major", "NAT pool absent."),
        ("MISSING_NAT_SOURCE", "major", "NAT inside source binding absent."),
        ("MISSING_NAT_ACL", "major", "No ACL exists on NAT pool."),
        ("MISSING_NAT_OVERLOAD", "major", "NAT pool not overloaded when required."),
        ("MISSING_NAT_VALID_POOL", "major", "No valid NAT pool found."),
        ("MISSING_INTERFACE_CONFIG", "major", "Interface entirely unconfigured."),
        ("MISSING_IP", "major", "IP address absent on interface."),
        ("MISSING_CLOCK_RATE", "minor", "Clock rate not configured on DCE interface."),
        ("MISSING_SHUTDOWN", "minor", "Interface not shutdown when required."),
        ("MISSING_ENCAPSULATION", "major", "PPP encapsulation not configured."),
        ("MISSING_PPP_AUTH", "major", "PPP authentication not configured."),
        ("MISSING_PPP_ACCOUNT", "major", "Required PPP account does not exist."),
        ("MISSING_ACL", "major", "Required ACL absent."),
        ("MISSING_ACL_RULE", "major", "Required ACL rule absent."),
        ("MISSING_VLAN", "major", "Required VLAN not created."),
        ("MISSING_VLAN_INTERFACE", "major", "Required SVI not configured."),
        ("MISSING_VLAN_NAME", "minor", "VLAN created but not named."),
        ("MISSING_TRUNK_PORT", "major", "Trunk not configured on required port."),
        ("MISSING_TRUNK_ALLOWED_VLAN", "major", "Required VLAN not allowed on trunk."),
        ("MISSING_PORT_SECURITY", "major", "Port security not enabled on required port."),
        ("MISSING_STP_MODE", "minor", "Spanning tree mode not configured."),
        ("MISSING_STP_PRIORITY", "major", "Bridge priority not set on required VLAN."),
        ("MISSING_STP_ROOT_FORCED", "major", "Root bridge not forced to be established."),
        ("MISSING_STP_PORTFAST", "minor", "PortFast not configured on access port."),
        ("MISSING_STP_BPDU_GUARD", "minor", "BPDU guard not configured."),
        ("MISSING_ETHERCHANNEL", "major", "EtherChannel group absent."),
        ("MISSING_ETHERCHANNEL_MEMBER", "major", "Interface missing from port-channel."),
        ("MISSING_PORTCHANNEL_TRUNK", "major", "Port-channel not configured as trunk."),
        ("MISSING_PAGP_MODE", "major", "PAgP mode not configured."),
        ("MISSING_LACP_MODE", "major", "LACP mode not configured."),
        ("MISSING_LACP_FAST_TIMER", "minor", "LACP fast timer not configured."),
        ("MISSING_VTY_LOGIN", "minor", "VTY login not configured."),
        ("MISSING_LINE_PASSWORD", "major", "No password specified on lines."),
        ("MISSING_USER", "major", "Required user account absent."),
        ("EXTRA_ROUTING_PROTOCOL", "major", "Routing protocol configured when not required."),
        ("EXTRA_ROUTING_NETWORK", "minor", "Extra network advertised."),
        ("EXTRA_ROUTING_INSTANCE", "major", "Multiple routing protocol instances configured."),
        ("EXTRA_STATIC_ROUTE", "minor", "Extra static route added."),
        ("EXTRA_DEFAULT_ROUTE", "minor", "Redundant default routes configured."),
        ("EXTRA_STATIC_CONFIGURED", "minor", "Static routes installed when they should be none."),
        ("EXTRA_REDISTRIBUTE", "minor", "Default routes redistributed when not required."),
        ("EXTRA_DHCP_POOL", "major", "Unrequired DHCP pool configured."),
        ("EXTRA_DHCP_ADVERTISING", "minor", "DHCP pool advertising when not required."),
        ("EXTRA_NAT_OVERLOAD", "major", "NAT pool overloaded when it should not be."),
        ("EXTRA_NAT_INTERFACE_CONFIGURED", "major", "NAT configured on interface when not required."),
        ("EXTRA_NAT_SOURCE", "minor", "Extra NAT inside source binding."),
        ("EXTRA_IFACE_CONFIGURED", "minor", "Interface configured when it should not have been."),
        ("EXTRA_PPP_ENABLED", "major", "PPP enabled when it should not be."),
        ("EXTRA_ACL", "minor", "Extra ACL created."),
        ("EXTRA_ACL_RULE", "minor", "Extra ACL rule added."),
        ("EXTRA_ACL_APPLIED", "major", "ACL applied to wrong interface."),
        ("EXTRA_VLAN", "minor", "Extra VLAN created."),
        ("EXTRA_VTY_CONFIG", "minor", "Extra VTY command added."),
        ("EXTRA_DESCRIPTION", "minor", "Interface description added when not required."),
        ("EXTRA_USER", "minor", "Extra user account created."),
        ("EXTRA_TRUNK_ALLOWED_VLAN", "minor", "Extra VLAN allowed on trunk."),
        ("EXTRA_STP_PRIORITY", "minor", "Extra STP priority configured."),
        ("EXTRA_ETHERCHANNEL", "minor", "Extra port-channel group created."),
        ("EXTRA_ETHERCHANNEL_MEMBER", "minor", "Extra interface added to port-channel."),
        ("EXTRA_LINE_ENABLED", "major", "Line access enabled when it should not be."),
    ]

    pattern_map = {
        "MISMATCH_HOSTNAME": [r"^show_running_config\.hostname$"],
        "MISSING_HOSTNAME": [r"^show_running_config\.hostname$"],
        "MISMATCH_MOTD": [r"^show_running_config\.banner_motd$"],
        "MISSING_MOTD": [r"^show_running_config\.banner_motd$"],
        "EXTRA_MOTD": [r"^show_running_config\.banner_motd$"],
        "MISMATCH_ROUTING_PROTOCOL": [r"^show_running_config\.routing\.protocol$"],
        "MISSING_ROUTING_PROTOCOL": [r"^show_running_config\.routing\.protocol$"],
        "EXTRA_ROUTING_PROTOCOL": [r"^show_running_config\.routing\.protocol$"],
        "MISMATCH_ROUTING_NETWORK": [r"\.routing\..+\.networks"],
        "MISSING_ROUTING_NETWORK": [r"\.routing\..+\.networks"],
        "EXTRA_NETWORK_ADVERTISED": [r"\.routing\..+\.networks"],
        "MISMATCH_AUTO_SUMMARY": [r"\.auto_summary$"],
        "MISMATCH_ROUTING_AUTO_SUMMARY": [r"\.auto_summary$"],
        "MISMATCH_ROUTING_PASSIVE": [r"\.passive_interface"],
        "MISSING_PASSIVE_INTERFACE": [r"\.passive_interface"],
        "MISMATCH_ROUTING_REDISTRIBUTE": [r"\.redistribute"],
        "MISSING_REDISTRIBUTE": [r"\.redistribute"],
        "EXTRA_REDISTRIBUTE": [r"\.redistribute"],
        "EXTRA_ROUTING_INSTANCE": [r"\.routing\..+\.instances$"],
        "MISMATCH_RIP_VERSION": [r"\.rip\..+\.version$"],
        "MISMATCH_EIGRP_AS_INCORRECT": [r"\.eigrp\..+\.asn$"],
        "MISMATCH_OSPF_AREA_ID_SINGLE": [r"\.ospf\.area_validation$"],
        "MISMATCH_OSPF_AREA_0_MISSING": [r"\.ospf\.area_validation$"],
        "MISSING_OSPF_POINT_TO_POINT": [r"\.ospf_network_type$"],
        "MISMATCH_STATIC_ROUTE_NETWORK": [r"\.static_routes"],
        "MISMATCH_STATIC_ROUTE_NEXTHOP": [r"\.static_routes"],
        "MISSING_STATIC_ROUTE": [r"\.static_routes"],
        "EXTRA_STATIC_ROUTE": [r"\.static_routes"],
        "MISMATCH_IP": [r"\.interfaces\..+\.ip$", r"\.show_ip_interface_brief\.interfaces\..+\.ip$"],
        "MISSING_IP": [r"\.interfaces\..+\.ip$"],
        "MISMATCH_MASK": [r"\.interfaces\..+\.mask$"],
        "MISMATCH_SHUTDOWN": [r"\.interfaces\..+\.shutdown$"],
        "MISSING_SHUTDOWN": [r"\.interfaces\..+\.shutdown$"],
        "MISMATCH_CLOCK_RATE": [r"\.interfaces\..+\.clock_rate$"],
        "MISSING_CLOCK_RATE": [r"\.interfaces\..+\.clock_rate$"],
        "MISMATCH_ENCAPSULATION": [r"\.interfaces\..+\.encapsulation$"],
        "MISSING_ENCAPSULATION": [r"\.interfaces\..+\.encapsulation$"],
        "MISMATCH_CONFIGURED_IF_SHUTDOWN": [r"\.configured_if_shutdown$"],
        "MISSING_DESCRIPTION": [r"\.interfaces\..+\.description$"],
        "EXTRA_DESCRIPTION": [r"\.interfaces\..+\.description$"],
        "MISMATCH_GATEWAY_ADDRESS": [r"\.switching\.default_gateway$"],
        "MISSING_DEFAULT_GATEWAY": [r"\.switching\.default_gateway$"],
        "MISMATCH_SUB_INT_VLAN": [r"\.subinterface$"],
        "MISMATCH_VLAN_INTERFACE": [r"\.Vlan\.interface$"],
        "MISSING_VLAN_INTERFACE": [r"\.interfaces\.Vlan\d+$"],
        "EXTRA_IFACE_CONFIGURED": [r"\.interfaces\..+$"],
        "MISSING_INTERFACE_CONFIG": [r"\.interfaces\..+$"],
        "MISMATCH_PPP_AUTH": [r"\.ppp_authentication$"],
        "MISSING_PPP_AUTH": [r"\.ppp_authentication$"],
        "MISMATCH_PPP_USERNAME": [r"\.ppp_chap_hostname$", r"\.ppp_pap_username$"],
        "MISMATCH_PPP_PASSWORD": [r"\.ppp_chap_password"],
        "MISSING_PPP_ACCOUNT": [r"\.ppp_chap_hostname$"],
        "MISMATCH_ACL_TYPE": [r"\.access_lists\..+\.type$"],
        "MISMATCH_ACL_RULE": [r"\.access_lists\..+\.rules"],
        "MISSING_ACL": [r"\.access_lists\..+$"],
        "MISSING_ACL_RULE": [r"\.access_lists\..+\.rules"],
        "EXTRA_ACL": [r"\.access_lists\..+$"],
        "EXTRA_ACL_RULE": [r"\.access_lists\..+\.rules"],
        "EXTRA_ACL_APPLIED": [r"\.access_groups"],
        "MISSING_ACL_APPLIED": [r"\.access_lists\..+\.applied$", r"\.access_groups"],
        "MISMATCH_ACL_POINTLESS_NORMAL": [r"\.access_lists\..+\.rules\.\d+$"],
        "MISMATCH_ACL_POINTLESS_DEFAULT": [r"\.access_lists\..+\.rules\.\d+$"],
        "MISMATCH_SWITCHPORT_MODE": [r"\.interfaces\..+\.switchport_mode$"],
        "MISMATCH_ACCESS_VLAN": [r"\.interfaces\..+\.access_vlan$"],
        "MISMATCH_VLAN_ID": [r"\.show_vlan_brief\.vlans\.vlan$"],
        "MISMATCH_VLAN_NAME": [r"\.show_vlan_brief\.vlans\.\d+\.name$"],
        "MISSING_VLAN": [r"\.show_vlan_brief\.vlans\.\d+$"],
        "MISSING_VLAN_NAME": [r"\.show_vlan_brief\.vlans\.\d+\.name$"],
        "EXTRA_VLAN": [r"\.show_vlan_brief\.vlans\.\d+$"],
        "MISMATCH_TRUNK_ENCAPSULATION": [r"\.trunk_encapsulation$", r"\.show_interfaces_trunk\..*\.encapsulation$"],
        "MISMATCH_TRUNK_MODE": [r"\.show_interfaces_trunk\..*\.mode$"],
        "MISMATCH_TRUNK_NATIVE_VLAN": [r"\.trunk_native_vlan$", r"\.show_interfaces_trunk\..*\.native_vlan$"],
        "MISMATCH_TRUNK_ALLOWED_VLANS": [r"\.trunk_allowed_vlans$"],
        "MISSING_TRUNK_PORT": [r"\.show_interfaces_trunk\.trunks\..+$"],
        "MISSING_TRUNK_ALLOWED_VLAN": [r"\.trunk_allowed_vlans$"],
        "EXTRA_TRUNK_ALLOWED_VLAN": [r"\.trunk_allowed_vlans$"],
        "MISMATCH_PORT_SECURITY_MAX": [r"\.port_security\.maximum$"],
        "MISMATCH_PORT_SECURITY_VIOLATION": [r"\.port_security\.violation$"],
        "MISMATCH_PORT_SECURITY_STICKY": [r"\.port_security\.sticky$"],
        "MISSING_PORT_SECURITY": [r"\.port_security\.enabled$"],
        "MISMATCH_STP_MODE": [r"\.spanning_tree\.mode$"],
        "MISSING_STP_MODE": [r"\.spanning_tree\.mode$"],
        "MISMATCH_STP_PRIORITY": [r"\.spanning_tree\.vlan_priorities", r"\.spanning_tree\.vlan_root"],
        "MISSING_STP_PRIORITY": [r"\.spanning_tree\.vlan_priorities"],
        "EXTRA_STP_PRIORITY": [r"\.spanning_tree\.vlan_priorities"],
        "MISMATCH_STP_PORTFAST": [r"\.stp_portfast$"],
        "MISSING_STP_PORTFAST": [r"\.stp_portfast$"],
        "MISMATCH_STP_BPDU_GUARD": [r"\.stp_bpduguard$"],
        "MISSING_STP_BPDU_GUARD": [r"\.stp_bpduguard$"],
        "MISMATCH_STP_ROOT_GUARD": [r"\.stp_root_guard$"],
        "MISMATCH_STP_PORT_COST": [r"\.stp_cost$"],
        "MISMATCH_STP_PORT_PRIORITY": [r"\.stp_port_priority$"],
        "MISMATCH_ETHERCHANNEL_GROUP": [r"\.channel_group\.group$"],
        "MISMATCH_ETHERCHANNEL_MODE": [r"\.channel_group\.mode$"],
        "MISSING_ETHERCHANNEL": [r"\.channel_group"],
        "MISMATCH_ETHERCHANNEL_LOAD_BALANCE": [r"\.etherchannel\.load_balance$"],
        "MISMATCH_LACP_PORT_PRIORITY": [r"\.lacp_port_priority$"],
        "MISMATCH_LACP_SYSTEM_PRIORITY": [r"\.lacp_system_priority$"],
        "MISMATCH_VTY_TRANSPORT": [r"\.vty\.transport$"],
        "MISSING_VTY_LOGIN": [r"\.vty\.login$"],
        "MISMATCH_LINE_PASSWORD": [r"\.(vty|console)\.password"],
        "MISSING_LINE_PASSWORD": [r"\.(vty|console)\.password"],
        "MISMATCH_HTTP_SERVER": [r"\.http_server"],
        "MISMATCH_USER_PRIVILEGE": [r"\.users\..+\.privilege$"],
        "MISMATCH_USER_AUTH_TYPE": [r"\.users\..+\.auth_type$"],
        "MISSING_USER": [r"\.users\..+$"],
        "EXTRA_USER": [r"\.users\..+$"],
        "MISMATCH_DHCP_POOL": [r"\.dhcp_pools\..+$"],
        "MISSING_DHCP_POOL": [r"\.dhcp_pools\..+$"],
        "EXTRA_DHCP_POOL": [r"\.dhcp_pools\..+$"],
        "MISMATCH_DHCP_EXCLUDED": [r"\.dhcp_excluded"],
        "MISSING_DHCP_EXCLUDED": [r"\.dhcp_excluded"],
        "MISMATCH_DHCP_BAD_EXCLUSION": [r"\.dhcp_excluded\.bad_exclusion$"],
        "MISMATCH_NAT_DIRECTION": [r"\.nat\.(inside|outside)_interfaces"],
        "MISSING_NAT_INSIDE": [r"\.nat\.inside_interfaces"],
        "MISSING_NAT_OUTSIDE": [r"\.nat\.outside_interfaces"],
        "MISMATCH_NAT_POOL": [r"\.nat\.pools"],
        "MISSING_NAT_POOL": [r"\.nat\.pools"],
        "MISMATCH_NAT_POOL_PREFIX": [r"\.nat\.pools\..+\.(netmask|prefix)"],
        "MISMATCH_NAT_POOL_RANGE_OUTSIDE": [r"\.nat\.pools\..+\.range$"],
        "MISMATCH_NAT_POOL_RANGE_INSIDE": [r"\.nat\.pools\..+\.range$"],
        "MISMATCH_NAT_ACL_BINDING": [r"\.nat\.inside_source"],
        "MISSING_NAT_SOURCE": [r"\.nat\.inside_source"],
        "EXTRA_NAT_SOURCE": [r"\.nat\.inside_source"],
        "EXTRA_NAT_OVERLOAD": [r"\.nat\..*overload"],
        "MISMATCH_TRUNK_IP_ASSIGNED": [r"\.trunk_ip_assigned$"],
        "EXTRA_PPP_ENABLED": [r"\.extra_ppp$"],
        "MISSING_NAT_ACL": [r"\.nat\.pools\..+\.acl_binding$"],
        "MISSING_DHCP_EMPTY_POOL": [r"\.dhcp_pools\..+\.network$"],
        "MISSING_ETHERCHANNEL_MEMBER": [r"\.show_etherchannel_summary\.groups\..+\.members"],
        "EXTRA_ETHERCHANNEL_MEMBER": [r"\.show_etherchannel_summary\.groups\..+\.members"],
        "EXTRA_ETHERCHANNEL": [r"\.show_etherchannel_summary\.groups\.\d+$"],
    }

    # ACCMS section grouping for UI — maps code prefixes to spec sections
    section_map = {
        "HOSTNAME": "3.2.1 Hostname & Banner",
        "MOTD": "3.2.1 Hostname & Banner",
        "ROUTING": "3.2.2 Interior Routing",
        "IRP": "3.2.2 Interior Routing",
        "AUTO": "3.2.2 Interior Routing",
        "NETWORK": "3.2.2 Interior Routing",
        "REDISTRIBUTE": "3.2.2 Interior Routing",
        "RIP": "3.2.2 Interior Routing",
        "EIGRP": "3.2.2 Interior Routing",
        "OSPF": "3.2.3 OSPF Specific",
        "STATIC": "3.2.4 Static Routes",
        "DEFAULT": "3.2.4 Static Routes",
        "DHCP": "3.2.5 DHCP",
        "NAT": "3.2.6 NAT",
        "IP": "3.2.7 Layer 3 Interface",
        "MASK": "3.2.7 Layer 3 Interface",
        "GATEWAY": "3.2.7 Layer 3 Interface",
        "HOST": "3.2.7 Layer 3 Interface",
        "TRUNK": "3.2.11 Trunk",
        "SUB": "3.2.7 Layer 3 Interface",
        "LINK": "3.2.7 Layer 3 Interface",
        "SHUTDOWN": "3.2.7 Layer 3 Interface",
        "CONFIGURED": "3.2.7 Layer 3 Interface",
        "CLOCK": "3.2.7 Layer 3 Interface",
        "DESCRIPTION": "3.2.7 Layer 3 Interface",
        "IFACE": "3.2.7 Layer 3 Interface",
        "INTERFACE": "3.2.7 Layer 3 Interface",
        "ENCAPSULATION": "3.2.8 PPP",
        "PPP": "3.2.8 PPP",
        "ACL": "3.2.9 ACL",
        "SWITCHPORT": "3.2.10 Switchport & VLAN",
        "ACCESS": "3.2.10 Switchport & VLAN",
        "VLAN": "3.2.10 Switchport & VLAN",
        "PORT": "3.2.12 Port Security",
        "STP": "3.2.13 Spanning Tree",
        "ETHERCHANNEL": "3.2.14 EtherChannel",
        "PORTCHANNEL": "3.2.14 EtherChannel",
        "PAGP": "3.2.15 PAgP & LACP",
        "LACP": "3.2.15 PAgP & LACP",
        "VTY": "3.2.16 Line (VTY/Console)",
        "LINE": "3.2.16 Line (VTY/Console)",
        "USER": "3.2.17 System",
        "HTTP": "3.2.17 System",
    }

    def _get_section(code):
        parts = code.split("_")
        # Try matching from the second token (skip MISMATCH/MISSING/EXTRA)
        for i in range(1, len(parts)):
            token = parts[i].upper()
            if token in section_map:
                return section_map[token]
        return "3.2.17 System"

    rules = []
    seen = set()
    for code, severity, description in accms_outcomes:
        if code in seen:
            continue
        seen.add(code)
        prefix = code.split("_", 1)[0]
        statuses = None
        if prefix == "MISMATCH":
            statuses = ["mismatch"]
        elif prefix == "MISSING":
            statuses = ["missing"]
        elif prefix == "EXTRA":
            statuses = ["extra"]
        category = code.split("_", 2)[1].lower() if "_" in code else "rule"
        subcategory = "_".join(code.split("_")[2:]).lower() if code.count("_") >= 2 else ""
        section = _get_section(code)
        rules.append(
            {
                "id": code,
                "code": code,
                "category": category,
                "subcategory": subcategory,
                "section": section,
                "description": description,
                "severity": severity,
                "enabled": True,
                "statuses": statuses,
                "patterns": pattern_map.get(code, []),
            }
        )
    return rules


def load_rubric_rules():
    defaults = _default_rubric_rules()
    default_ids = {rule.get("id") for rule in defaults if isinstance(rule, dict)}
    if not RUBRIC_RULES_PATH.exists():
        RUBRIC_RULES_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(RUBRIC_RULES_PATH, "w") as handle:
            json.dump(defaults, handle, indent=2)
        return defaults
    try:
        with open(RUBRIC_RULES_PATH, "r") as handle:
            data = json.load(handle) or []
            if isinstance(data, dict):
                data = data.get("rules", [])
            if not isinstance(data, list):
                data = []
            filtered = [
                rule
                for rule in data
                if isinstance(rule, dict) and rule.get("id") in default_ids
            ]
            existing = {rule.get("id"): rule for rule in filtered}
            merged = list(filtered)
            for rule in defaults:
                if rule.get("id") not in existing:
                    merged.append(rule)
            # Persist merged rules so new defaults appear in UI.
            with open(RUBRIC_RULES_PATH, "w") as out:
                json.dump(merged, out, indent=2)
            return merged
    except Exception:
        return defaults


def save_rubric_rules(rules):
    if not isinstance(rules, list):
        raise ValueError("Rubric rules must be a list.")
    cleaned = []
    for idx, rule in enumerate(rules):
        if not isinstance(rule, dict):
            continue
        rid = rule.get("id") or f"rule_{idx+1}"
        cleaned.append(
            {
                "id": rid,
                "category": rule.get("category") or "",
                "subcategory": rule.get("subcategory") or "",
                "description": rule.get("description") or "",
                "severity": (rule.get("severity") or "minor").lower(),
                "enabled": bool(rule.get("enabled", True)),
                "patterns": rule.get("patterns") or [],
            }
        )
    RUBRIC_RULES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RUBRIC_RULES_PATH, "w") as handle:
        json.dump(cleaned, handle, indent=2)
    return cleaned


def load_grading_policy():
    if not GRADING_POLICY_PATH.exists():
        GRADING_POLICY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(GRADING_POLICY_PATH, "w") as handle:
            json.dump(_default_grading_policy(), handle, indent=2)
        return _default_grading_policy()
    try:
        with open(GRADING_POLICY_PATH, "r") as handle:
            data = json.load(handle) or {}
    except Exception:
        data = {}
    policy = _default_grading_policy()
    policy.update({k: v for k, v in data.items() if v is not None})
    return policy


def save_grading_policy(data):
    policy = _default_grading_policy()
    policy.update(data or {})
    GRADING_POLICY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(GRADING_POLICY_PATH, "w") as handle:
        json.dump(policy, handle, indent=2)
    return policy


def _safe_resolve_child(base: Path, target: Path) -> Path:
    base = base.resolve()
    target = target.resolve()
    if base == target or base in target.parents:
        return target
    return None


def _iter_session_students(target_path: str):
    if not target_path or not os.path.isdir(target_path):
        return []
    students = []
    for entry in sorted(os.listdir(target_path)):
        full = os.path.join(target_path, entry)
        if os.path.isdir(full):
            students.append({"student_id": entry, "path": full})
    return students


def _load_student_results(student_dir: Path, student_id: str):
    if not student_dir.is_dir():
        return None

    results_dir = student_dir / "results"
    if not results_dir.is_dir():
        return None

    host_results = {}
    all_items = []
    for file_path in sorted(results_dir.glob("*_result.json")):
        try:
            with open(file_path, "r") as handle:
                data = json.load(handle) or {}
        except Exception:
            continue

        hostname = data.get("hostname") or file_path.stem.replace("_result", "")
        results = data.get("results") or []
        host_results[hostname] = {
            "hostname": hostname,
            "grading_mode": data.get("grading_mode"),
            "template_name": data.get("template_name"),
            "student_show_run_file": data.get("student_show_run_file"),
            "student_parsed_file": data.get("student_parsed_file"),
            "results": results,
        }
        for item in results:
            item_copy = dict(item)
            item_copy["hostname"] = hostname
            all_items.append(item_copy)

    if not host_results:
        return None

    template_name = None
    grading_mode = None
    for host in host_results.values():
        if not template_name:
            template_name = host.get("template_name")
        if not grading_mode:
            grading_mode = host.get("grading_mode")

    return {
        "student_id": student_id,
        "template_name": template_name,
        "grading_mode": grading_mode,
        "hostnames": host_results,
        "items": all_items,
    }


def _classify_items(items, policy, rubric_rules=None):
    rubric_compiled = []
    for rule in rubric_rules or []:
        if not rule.get("enabled", True):
            continue
        patterns = rule.get("patterns") or []
        compiled_patterns = []
        for pattern in patterns:
            try:
                compiled_patterns.append(re.compile(pattern))
            except re.error:
                continue
        statuses = rule.get("statuses")
        if isinstance(statuses, str):
            statuses = [s.strip().lower() for s in statuses.split(",") if s.strip()]
        elif isinstance(statuses, list):
            statuses = [str(s).strip().lower() for s in statuses if str(s).strip()]
        else:
            statuses = None
        if compiled_patterns:
            rubric_compiled.append((rule, compiled_patterns, statuses))

    summary = {
        "correct": 0,
        "missing": 0,
        "extra": 0,
        "mismatch": 0,
        "major": 0,
        "minor": 0,
        "skipped": 0,
    }

    # Build a lookup from code -> rule for outcome_code matching
    rubric_by_code = {}
    for rule, _patterns, _statuses in rubric_compiled:
        code = rule.get("code") or rule.get("id")
        if code:
            rubric_by_code[code] = rule

    minor_rule_hits = set()

    classified = []
    for item in items:
        status = item.get("status")
        if status in summary:
            summary[status] += 1

        severity = None
        rule_id = None
        rule_code = None
        matched_rule = None
        if status in {"missing", "extra", "mismatch"}:
            # 1) Try matching by outcome_code from comparator
            item_code = item.get("outcome_code")
            if item_code and item_code in rubric_by_code:
                matched_rule = rubric_by_code[item_code]
            else:
                # 2) Fall back to regex pattern matching
                for rule, patterns, statuses in rubric_compiled:
                    if statuses and status not in statuses:
                        continue
                    if any(regex.search(item.get("feature", "")) for regex in patterns):
                        matched_rule = rule
                        break
            if matched_rule:
                severity = (matched_rule.get("severity") or "minor").lower()
                rule_id = matched_rule.get("id")
                rule_code = matched_rule.get("code")
            else:
                severity = "minor"

            if severity == "major":
                summary["major"] += 1
            else:
                if rule_id:
                    if rule_id not in minor_rule_hits:
                        summary["minor"] += 1
                        minor_rule_hits.add(rule_id)
                else:
                    summary["minor"] += 1

        item_copy = dict(item)
        if severity:
            item_copy["severity"] = severity
        if rule_id:
            item_copy["rule_id"] = rule_id
        if rule_code:
            item_copy["rule_code"] = rule_code
        classified.append(item_copy)

    return classified, summary


def _evaluate_pass_fail(summary, policy):
    major_threshold = int(policy.get("major_threshold") or 1)
    minor_threshold = int(policy.get("minor_threshold") or 5)
    failed = summary.get("major", 0) >= major_threshold
    if not failed:
        failed = summary.get("minor", 0) >= minor_threshold
    return not failed


def _build_session_reports(target_path: str):
    policy = load_grading_policy()
    rubric_rules = load_rubric_rules()
    reports = []
    for student in _iter_session_students(target_path):
        student_id = student.get("student_id")
        student_dir = Path(student.get("path") or "")
        report = _load_student_results(student_dir, student_id)
        if not report:
            reports.append(
                {
                    "student_id": student_id,
                    "status": "no_results",
                    "pass": False,
                    "summary": {
                        "correct": 0,
                        "missing": 0,
                        "extra": 0,
                        "mismatch": 0,
                        "major": 0,
                        "minor": 0,
                        "skipped": 0,
                    },
                    "hostnames": {},
                    "items": [],
                }
            )
            continue

        items, summary = _classify_items(report["items"], policy, rubric_rules)
        passed = _evaluate_pass_fail(summary, policy)
        report["items"] = items
        report["summary"] = summary
        report["pass"] = passed
        report["status"] = "graded"
        reports.append(report)

    return reports


def _acquire_ssh_connection(host, username, password, port=None):
    try:
        port_value = int(str(port)) if port is not None else 22
    except ValueError:
        port_value = 22
    client = remote_connect(host, username, password, port=port_value)
    if not client:
        return None, None, port_value
    shell = getattr(client, "_shell", None)
    if shell is None:
        try:
            shell = client.invoke_shell()
            client._shell = shell
        except Exception:
            shell = None
    return client, shell, port_value


def stream_json_line(obj):
    return json.dumps(obj) + "\n"


def _expand_path(path):
    """Expand ~ in user supplied paths."""
    return os.path.expanduser(path) if path else None


def _engine_student_logs_dir(exam_name, session_id, student_id, hostname=None):
    safe_exam = (str(exam_name or "").strip())
    safe_session = (str(session_id or "").strip())
    safe_student = (str(student_id or "").strip())
    if not all([safe_exam, safe_session, safe_student]):
        return None
    if safe_student.lower() in {"sample", "unknown"}:
        return None
    target_dir = ENGINE_STUDENTS_DIR / safe_exam / safe_session / safe_student
    if hostname:
        target_dir = target_dir / str(hostname).strip()
    return target_dir


def _delete_engine_student_logs_for_docs_target(target):
    try:
        relative = target.resolve().relative_to(DOCS_DIR)
    except Exception:
        return

    if len(relative.parts) < 2:
        return

    mirror_target = ENGINE_STUDENTS_DIR.joinpath(*relative.parts)
    if mirror_target.exists():
        shutil.rmtree(mirror_target)


def _save_output_to_engine_students(
    command, output, exam_name, session_id, student_id, hostname
):
    """
    Save command output under
    comparsion_engine/students/<exam_name>/<session_id>/<student_id>/<hostname>/.
    Only stores command logs (no config.json).
    """
    if not hostname:
        return None
    target_dir = _engine_student_logs_dir(exam_name, session_id, student_id, hostname)
    if target_dir is None:
        return None
    safe_command = command.replace(" ", "_").replace("/", "_")
    target_dir.mkdir(parents=True, exist_ok=True)
    file_path = target_dir / f"{safe_command}.txt"
    with open(file_path, "w", encoding="utf-8") as handle:
        handle.write(output)
    return str(file_path)


# -------------------------------------------------
# ✅ Directory Endpoints
# -------------------------------------------------
def _validate_directory_payload(data):
    exam_name = data.get("examName")
    session_id = data.get("sessionId")
    student_id = data.get("studentId")

    if not all([exam_name, session_id, student_id]):
        return (
            None,
            jsonify(
                {"status": "error", "message": "Missing examName/sessionId/studentId"}
            ),
            400,
        )
    return (exam_name, session_id, student_id), None, None


@app.route("/api/create_directory", methods=["POST"])
def api_create_directory():
    """
    Create the standard directory hierarchy for a student.
    """
    data = request.get_json() or {}
    validated, error_resp, status = _validate_directory_payload(data)
    if error_resp:
        return error_resp, status

    exam_name, session_id, student_id = validated
    base_path = os.path.expanduser(
        os.path.join("~/Documents", exam_name, session_id, student_id)
    )
    os.makedirs(base_path, exist_ok=True)
    return jsonify(
        {
            "status": "ok",
            "message": f"Directory ready: {base_path}",
            "path": base_path,
            "exam_name": exam_name,
            "session_id": session_id,
            "student_id": student_id,
        }
    )


@app.route("/api/select_directory", methods=["POST"])
def api_select_directory():
    """
    Reuse an existing directory path provided by the user.
    """
    data = request.get_json() or {}
    existing_path = _expand_path(data.get("existingPath"))
    if not existing_path:
        return (
            jsonify(
                {"status": "error", "message": "Missing existingPath for selection"}
            ),
            400,
        )

    if os.path.exists(existing_path):
        parts = Path(existing_path).parts
        if len(parts) >= 3:
            exam_name, session_id, student_id = parts[-3], parts[-2], parts[-1]
        else:
            exam_name = data.get("examName")
            session_id = data.get("sessionId")
            student_id = data.get("studentId")
        return jsonify(
            {
                "status": "ok",
                "message": f"Using existing directory: {existing_path}",
                "path": existing_path,
                "exam_name": exam_name,
                "session_id": session_id,
                "student_id": student_id,
            }
        )

    return (
        jsonify({"status": "error", "message": f"Path not found: {existing_path}"}),
        404,
    )


def _list_existing_directories():
    docs_path = Path.home() / "Documents"
    results = []
    if not docs_path.exists():
        return results

    for exam_dir in docs_path.iterdir():
        if not exam_dir.is_dir() or exam_dir.name.startswith("."):
            continue
        for session_dir in exam_dir.iterdir():
            if not session_dir.is_dir():
                continue
            for student_dir in session_dir.iterdir():
                if not student_dir.is_dir():
                    continue
                results.append(
                    {
                        "path": str(student_dir),
                        "exam_name": exam_dir.name,
                        "session_id": session_dir.name,
                        "student_id": student_dir.name,
                        "display": f"{exam_dir.name}/{session_dir.name}/{student_dir.name}",
                    }
                )
    return sorted(results, key=lambda x: x["display"])


def _list_existing_sessions():
    docs_path = Path.home() / "Documents"
    results = []
    if not docs_path.exists():
        return results

    for exam_dir in docs_path.iterdir():
        if not exam_dir.is_dir() or exam_dir.name.startswith("."):
            continue
        for session_dir in exam_dir.iterdir():
            if not session_dir.is_dir():
                continue
            results.append(
                {
                    "path": str(session_dir),
                    "exam_name": exam_dir.name,
                    "session_id": session_dir.name,
                    "display": f"{exam_dir.name}/{session_dir.name}",
                }
            )
    return sorted(results, key=lambda x: x["display"])


@app.route("/api/directories", methods=["GET"])
def api_list_directories():
    path_val = request.args.get("path")
    docs_path = (Path.home() / "Documents").resolve()
    
    # If a path is provided, use it as the "current" one, otherwise default to ~/Documents
    if path_val:
        try:
            current = Path(_expand_path(path_val)).resolve()
        except Exception:
            current = docs_path
    else:
        current = docs_path
            
    # Only return the managed "directories" list if we are explicitly at the managed root.
    # Otherwise, we want the frontend to fall back to 'loadSubfolders' to show the actual directory contents.
    directories = []
    if current == docs_path:
        directories = _list_existing_directories()

    return jsonify({
        "status": "ok", 
        "directories": directories,
        "current_path": str(current)
    })

@app.route("/api/subfolders", methods=["GET"])
def api_list_subfolders():
    path_val = request.args.get("path")
    
    # If path not provided, default to user home so they can see Documents, Downloads etc.
    if not path_val:
        target = Path.home()
    else:
        try:
            target = Path(_expand_path(path_val)).resolve()
        except:
            return jsonify({"status": "error", "message": "Invalid path"}), 400
        
    if not target.exists() or not target.is_dir():
         return jsonify({"status": "error", "message": "Path not found"}), 404
         
    subfolders = []
    try:
        # List directories only
        for item in target.iterdir():
            if item.is_dir() and not item.name.startswith("."):
                subfolders.append({
                    "name": item.name,
                    "path": str(item)
                })
        subfolders.sort(key=lambda x: x["name"].lower())
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
        
    return jsonify({
        "status": "ok", 
        "subfolders": subfolders,
        "current_path": str(target),
        "parent_path": str(target.parent)
    })


@app.route("/api/directories/bulk", methods=["POST"])
def api_bulk_directories():
    data = request.get_json() or {}
    exam_name = data.get("examName")
    session_id = data.get("sessionId")
    students = data.get("students") or []

    if not exam_name or not session_id or not students:
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "Missing examName/sessionId/students for bulk creation.",
                }
            ),
            400,
        )

    created = []
    base_docs_path = Path.home() / "Documents"

    for student in students:
        student_id = (student.get("id") or "").strip()
        if not student_id:
            continue
        student_dir = base_docs_path / exam_name / session_id / student_id
        student_dir.mkdir(parents=True, exist_ok=True)
        created.append(
            {
                "path": str(student_dir),
                "exam_name": exam_name,
                "session_id": session_id,
                "student_id": student_id,
                "display": f"{exam_name}/{session_id}/{student_id}",
            }
        )

    return jsonify({"status": "ok", "created": created})


# -------------------------------------------------
# ✅ Connection Test Endpoint
# -------------------------------------------------

@app.route("/api/connect", methods=["POST"])
def api_connect():
    """Stream connection progress to the client for serial/SSH tests."""
    data = request.get_json() or {}
    mode = (data.get("mode") or data.get("connection") or "").lower()

    def stream_error(message, trace=None):
        print(f"[API][connect] ERROR: {message}", flush=True)
        payload = {"type": "error", "msg": message}
        if trace:
            payload["trace"] = trace
        payload["done"] = True
        return stream_json_line(payload)

    if mode not in {"serial", "ssh"}:
        return Response(
            stream_error("Invalid connection type"), mimetype="text/plain", status=400
        )

    def serial_generator():
        global current_mode
        serial_cfg = data.get("serial") or {}
        with connection_lock:
            stored_port = last_used_serial_settings.get("port")
            stored_baud = last_used_serial_settings.get("baudrate", 9600)
            existing_hostname = serial_hostname or "device"
        port = serial_cfg.get("port") or stored_port or "/dev/ttyUSB0"
        baudrate = (
            serial_cfg.get("baudrate") or serial_cfg.get("baud") or stored_baud or 9600
        )
        with connection_lock:
            last_used_serial_settings["port"] = port
            last_used_serial_settings["baudrate"] = baudrate

        _close_serial_connection()
        _close_ssh_connection()

        yield stream_json_line(
            {
                "type": "progress",
                "msg": f"Connecting over serial: {port}",
            }
        )

        queue = SimpleQueue()

        def status_cb(message):
            queue.put(("progress", message))

        def worker():
            ser = None
            try:
                ser = connect_to_serial(
                    port=port,
                    baudrate=baudrate,
                    timeout=READ_TIMEOUT,
                    retry_interval=3,
                    max_retries=5,
                    status_cb=status_cb,
                )
                if not ser:
                    queue.put(("error", f"Failed to open serial port {port}"))
                    return

                queue.put(("progress", "Ensuring privileged access..."))
                enter_enable_mode(ser)
                queue.put(("progress", "Disabling paging..."))
                disable_paging(ser)
                try:
                    hostname = get_hostname(ser) or "device"
                except Exception:
                    hostname = "device"

                _update_serial_state(ser, port, baudrate, hostname)

                queue.put(("success", {"hostname": hostname, "port": port}))
            except Exception as exc:
                if ser:
                    try:
                        logout_close_connection(ser)
                    except Exception:
                        pass
                queue.put(("exception", (str(exc), traceback.format_exc())))

        threading.Thread(target=worker, daemon=True).start()

        while True:
            event, payload = queue.get()
            if event == "progress":
                print(f"[API][connect][serial] {payload}", flush=True)
                yield stream_json_line({"type": "progress", "msg": payload})
            elif event == "success":
                hostname = payload.get("hostname", "device")
                port_value = payload.get("port")
                print(
                    f"[API][connect][serial] Connected to {hostname} (port={port_value})",
                    flush=True,
                )
                yield stream_json_line(
                    {
                        "type": "success",
                        "msg": f"Connected to {hostname}",
                        "hostname": hostname,
                        "port": port_value,
                        "persistent": True,
                    }
                )
                yield stream_json_line(
                    {
                        "type": "done",
                        "success": True,
                        "hostname": hostname,
                        "port": port_value,
                    }
                )
                return
            elif event == "error":
                print(f"[API][connect][serial] ERROR: {payload}", flush=True)
                yield stream_json_line({"type": "error", "msg": payload})
                yield stream_json_line({"type": "done", "success": False})
                return
            elif event == "exception":
                msg, tb = payload
                print(f"[API][connect][serial] EXCEPTION: {msg}", flush=True)
                yield stream_json_line({"type": "error", "msg": msg, "trace": tb})
                yield stream_json_line({"type": "done", "success": False})
                return

    def ssh_generator():
        global current_mode
        ssh = data.get("ssh") or {}
        host = ssh.get("host") or data.get("host")
        user = ssh.get("username") or data.get("username")
        pwd = ssh.get("password") or data.get("password")
        raw_port = ssh.get("port") or data.get("port") or last_used_ssh_credentials.get(
            "port", 22
        )
        try:
            port_value = int(str(raw_port))
        except (TypeError, ValueError):
            port_value = 22

        if not all([host, user, pwd]):
            print("[API][connect][ssh] Missing credentials.", flush=True)
            yield stream_json_line(
                {
                    "type": "error",
                    "msg": "Missing SSH credentials (host, username, password).",
                }
            )
            yield stream_json_line({"type": "done", "success": False})
            return

        with connection_lock:
            active_client = ssh_client if _is_ssh_client_active(ssh_client) else None
            cached_host = last_used_ssh_credentials.get("host")
            cached_user = last_used_ssh_credentials.get("username")
            cached_port = last_used_ssh_credentials.get("port")
            cached_hostname = ssh_hostname or host

        if (
            active_client
            and cached_host == host
            and cached_user == user
            and (cached_port or port_value) == port_value
        ):
            print(f"[API][connect][ssh] Reusing SSH session to {host}", flush=True)
            with connection_lock:
                current_mode = "ssh"
            yield stream_json_line(
                {
                    "type": "progress",
                    "msg": f"Reusing existing SSH session to {host}",
                }
            )
            yield stream_json_line(
                {
                    "type": "success",
                    "msg": f"Connected to {cached_hostname}",
                    "hostname": cached_hostname,
                    "host": host,
                    "port": port_value,
                    "persistent": True,
                }
            )
            yield stream_json_line(
                {
                    "type": "done",
                    "success": True,
                    "hostname": cached_hostname,
                    "host": host,
                    "port": port_value,
                }
            )
            return

        _close_serial_connection()
        _close_ssh_connection()

        print(
            f"[API][connect][ssh] Connecting to {host}:{port_value} ...", flush=True
        )
        yield stream_json_line(
            {
                "type": "progress",
                "msg": f"Connecting to {host}:{port_value} via SSH...",
            }
        )

        try:
            result = _acquire_ssh_connection(host, user, pwd, port_value)
            client, shell, resolved_port = result
            if not client:
                print(
                    f"[API][connect][ssh] Connection to {host}:{port_value} failed.",
                    flush=True,
                )
                yield stream_json_line(
                    {"type": "error", "msg": "SSH connection failed."}
                )
                yield stream_json_line({"type": "done", "success": False})
                return

            print("[API][connect][ssh] Entering enable mode...", flush=True)
            yield stream_json_line(
                {"type": "progress", "msg": "Entering enable mode..."}
            )
            enter_enable_mode_remote(client)
            print("[API][connect][ssh] Disabling paging...", flush=True)
            yield stream_json_line({"type": "progress", "msg": "Disabling paging..."})
            disable_paging_remote(client)
            try:
                hostname = get_hostname_remote(client) or host
                print(f"[API][connect][ssh] Detected hostname: {hostname}", flush=True)
                yield stream_json_line(
                    {"type": "progress", "msg": f"Detected hostname: {hostname}"}
                )
            except Exception:
                hostname = host
                print("[API][connect][ssh] Hostname detection failed.", flush=True)
                yield stream_json_line(
                    {
                        "type": "progress",
                        "msg": "Connected but hostname detection failed.",
                    }
                )

            _update_ssh_state(client, host, user, pwd, hostname, resolved_port)

            print(f"[API][connect][ssh] Connected to {hostname}", flush=True)
            yield stream_json_line(
                {
                    "type": "success",
                    "msg": f"Connected to {hostname}",
                    "hostname": hostname,
                    "host": host,
                    "port": resolved_port,
                    "persistent": True,
                }
            )
            yield stream_json_line(
                {
                    "type": "done",
                    "success": True,
                    "hostname": hostname,
                    "host": host,
                    "port": resolved_port,
                }
            )
        except Exception as exc:
            print(f"[API][connect][ssh] EXCEPTION: {exc}", flush=True)
            yield stream_json_line(
                {"type": "error", "msg": str(exc), "trace": traceback.format_exc()}
            )
            yield stream_json_line({"type": "done", "success": False})

    generator = serial_generator() if mode == "serial" else ssh_generator()
    return Response(stream_with_context(generator), mimetype="text/plain")



# -------------------------------------------------
# ✅ Get Commands
# -------------------------------------------------
@app.route("/api/commands", methods=["GET"])
def api_get_commands():
    try:
        commands = load_commands()
        return jsonify({"status": "ok", "commands": commands})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/commands", methods=["POST"])
def api_add_command():
    data = request.get_json() or {}
    command = (data.get("command") or "").strip()
    if not command:
        return jsonify({"status": "error", "message": "Command text is required."}), 400

    commands = load_commands()
    if command in commands:
        return jsonify({"status": "error", "message": "Command already exists."}), 400

    commands.append(command)
    save_commands(commands)
    return jsonify({"status": "ok", "commands": commands})


@app.route("/api/commands", methods=["DELETE"])
def api_delete_command():
    data = request.get_json() or {}
    command = (data.get("command") or "").strip()
    if not command:
        return jsonify({"status": "error", "message": "Command text is required."}), 400

    commands = load_commands()
    if command not in commands:
        return jsonify({"status": "error", "message": "Command not found."}), 404

    commands = [c for c in commands if c != command]
    save_commands(commands)
    return jsonify({"status": "ok", "commands": commands})


# -------------------------------------------------
# ✅ Save Log Endpoint
# -------------------------------------------------
@app.route("/api/save_log", methods=["POST"])
def api_save_log():
    data = request.get_json() or {}
    exam_name = data.get("exam_name")
    session_id = data.get("session_id")
    student_id = data.get("student_id")
    filename = data.get("filename", "log.txt")
    content = data.get("content", "")

    if not (exam_name and session_id and student_id):
        return jsonify({"status": "error", "message": "Missing directory info"}), 400

    base_dir = os.path.expanduser(
        os.path.join("~/Documents", exam_name, session_id, student_id)
    )
    os.makedirs(base_dir, exist_ok=True)
    path = os.path.join(base_dir, filename)

    try:
        with open(path, "w") as f:
            f.write(content)
        return jsonify({"status": "ok", "message": f"Saved log to {path}"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# -------------------------------------------------
# ✅ Execute Endpoint
# -------------------------------------------------
def _ensure_base_path(data):
    """
    Resolve the base directory for log storage based on payload.
    """
    mode = data.get("log_mode", "create")
    log_dir = data.get("log_dir")
    exam_name = data.get("exam_name")
    session_id = data.get("session_id")
    student_id = data.get("student_id")

    if mode == "existing":
        if not log_dir:
            raise ValueError("Missing log_dir for existing directory mode.")
        expanded = _expand_path(log_dir)
        if not expanded or not os.path.exists(expanded):
            raise FileNotFoundError(f"Existing directory not found: {log_dir}")
        return expanded, exam_name, session_id, student_id

    if not all([exam_name, session_id, student_id]):
        raise ValueError("Missing exam/session/student details for directory creation.")

    base_path = os.path.expanduser(
        os.path.join("~/Documents", exam_name, session_id, student_id)
    )
    os.makedirs(base_path, exist_ok=True)
    return base_path, exam_name, session_id, student_id



@app.route("/api/execute", methods=["POST"])
def api_execute():
    data = request.get_json() or {}
    commands = data.get("commands") or []
    target_device = data.get("deviceId") or data.get("target_device")
    requested_mode = (
        data.get("mode") or data.get("connection") or current_mode or "serial"
    ).lower()

    print(
        f"[DEBUG] /api/execute called with mode={requested_mode}, current_mode={current_mode}, deviceId={target_device}",
        flush=True,
    )

    if not commands:
        return jsonify({"status": "error", "message": "No commands provided"}), 400
    if not requested_mode:
        return (
            jsonify({"status": "error", "message": "No connection mode selected."}),
            400,
        )
    if requested_mode not in {"serial", "ssh"}:
        return (
            jsonify({"status": "error", "message": "Invalid connection type"}),
            400,
        )

    try:
        base_path, exam_name, session_id, student_id = _ensure_base_path(data)
    except FileNotFoundError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400

    def generate():
        hostname = None
        files_written = []
        skip_config = bool(data.get("skip_config"))

        def run_serial():
            global current_mode
            nonlocal hostname
            serial_payload = data.get("serial") or {}
            with connection_lock:
                stored_port = last_used_serial_settings.get("port")
                stored_baud = last_used_serial_settings.get("baudrate", 9600)
                existing_ser = serial_conn if serial_conn and serial_conn.is_open else None
                stored_hostname = serial_hostname or "device"
            port = serial_payload.get("port") or stored_port or "/dev/ttyUSB0"
            baudrate = (
                serial_payload.get("baudrate")
                or serial_payload.get("baud")
                or stored_baud
                or 9600
            )
            if not port:
                yield stream_json_line(
                    {
                        "type": "error",
                        "msg": "Serial mode selected but no port configured. Please connect via serial first.",
                    }
                )
                return False
            with connection_lock:
                last_used_serial_settings["port"] = port
                last_used_serial_settings["baudrate"] = baudrate

            ser = None
            reuse = False
            _close_ssh_connection()
            _close_serial_connection()
            yield stream_json_line(
                {
                    "type": "progress",
                    "msg": f"Connecting over serial: {port}",
                    "progress_pct": 0,
                }
            )
            try:
                ser = connect_to_serial(
                    port=port,
                    baudrate=baudrate,
                    timeout=READ_TIMEOUT,
                    retry_interval=3,
                    max_retries=5,
                )
            except Exception as exc:
                yield stream_json_line(
                    {
                        "type": "error",
                        "msg": f"Failed to open serial port {port}: {exc}",
                    }
                )
                return False
            try:
                yield stream_json_line(
                    {
                        "type": "progress",
                        "msg": "Waking device and syncing prompt...",
                        "progress_pct": 0,
                    }
                )
                try:
                    wait_for_prompt(ser, [">", "#"], timeout=READ_TIMEOUT, wake=True)
                except Exception:
                    wait_for_prompt(ser, [">", "#"], timeout=READ_TIMEOUT + 5, wake=True)
            except Exception as exc:
                logout_close_connection(ser)
                yield stream_json_line(
                    {
                        "type": "error",
                        "msg": f"Serial initialization failed: prompt not detected ({exc})",
                    }
                )
                return False
            try:
                yield stream_json_line(
                    {
                        "type": "progress",
                        "msg": "Ensuring privileged access...",
                        "progress_pct": 0,
                    }
                )
                enter_enable_mode(ser)
                yield stream_json_line(
                    {
                        "type": "progress",
                        "msg": "Disabling paging...",
                        "progress_pct": 0,
                    }
                )
                disable_paging(ser)
                try:
                    hostname = get_hostname(ser) or "device"
                except Exception:
                    hostname = "device"
            except Exception as exc:
                logout_close_connection(ser)
                yield stream_json_line(
                    {"type": "error", "msg": f"Serial initialization failed: {exc}"}
                )
                return False
            _update_serial_state(ser, port, baudrate, hostname)

            yield stream_json_line(
                {
                    "type": "progress",
                    "msg": f"Connected to {hostname} via serial.",
                    "progress_pct": 0,
                }
            )

            local_ser = ser or serial_conn
            if not local_ser:
                yield stream_json_line(
                    {
                        "type": "error",
                        "msg": "Serial connection unavailable after setup.",
                    }
                )
                return False

            completed = 0
            total_commands = len(commands)
            for cmd in commands:
                yield stream_json_line({"type": "progress", "msg": f"Running '{cmd}'..."})
                try:
                    output = send_command(local_ser, cmd, timeout=30)
                    file_path = save_output_to_file(
                        cmd,
                        output,
                        exam_name,
                        student_id,
                        session_id,
                        target_device or hostname,
                        base_dir=base_path,
                    )
                    _save_output_to_engine_students(
                        cmd,
                        output,
                        exam_name,
                        session_id,
                        student_id,
                        target_device or hostname,
                    )
                    files_written.append(file_path)
                    completed += 1
                    pct = round((completed / total_commands) * 100) if total_commands else 100
                    yield stream_json_line(
                        {
                            "type": "progress",
                            "msg": f"Completed '{cmd}'.",
                            "cmd_done": True,
                            "progress_pct": pct,
                        }
                    )
                except Exception as exc:
                    del_partial_logs(base_path, exam_name, session_id, student_id, hostname)
                    yield stream_json_line(
                        {
                            "type": "error",
                            "msg": f"Command '{cmd}' failed: {exc}",
                        }
                    )
                    _close_serial_connection()
                    return False

            if not skip_config:
                # Build parsed config.json for the student device logs.
                try:
                    host_folder = target_device or hostname or "device"
                    host_dir = os.path.join(base_path, host_folder)
                    os.makedirs(host_dir, exist_ok=True)
                    config = parse_device_logs(files_written)
                    config_path = os.path.join(host_dir, "config.json")
                    with open(config_path, "w") as handle:
                        json.dump(config, handle, indent=4)
                    yield stream_json_line(
                        {"type": "result", "msg": f"Saved config.json to {config_path}"}
                    )
                except Exception as exc:
                    try:
                        host_folder = target_device or hostname or "device"
                        host_dir = os.path.join(base_path, host_folder)
                        os.makedirs(host_dir, exist_ok=True)
                        fallback = parse_device_logs([])
                        fallback["parse_error"] = str(exc)
                        config_path = os.path.join(host_dir, "config.json")
                        with open(config_path, "w") as handle:
                            json.dump(fallback, handle, indent=4)
                        yield stream_json_line(
                            {
                                "type": "error",
                                "msg": f"Failed to parse logs ({exc}). Wrote fallback config.json to {config_path}",
                            }
                        )
                    except Exception as exc2:
                        yield stream_json_line(
                            {
                                "type": "error",
                                "msg": f"Failed to save config.json: {exc}; fallback failed: {exc2}",
                            }
                        )
            
            # Close the port so the user can physically unplug the cable for the next queue item
            _close_serial_connection()
            return True

        def run_ssh():
            global current_mode
            nonlocal hostname
            ssh_payload = data.get("ssh") or {}
            with connection_lock:
                active_client = ssh_client if _is_ssh_client_active(ssh_client) else None
                cached_host = last_used_ssh_credentials.get("host")
                cached_user = last_used_ssh_credentials.get("username")
                cached_port = last_used_ssh_credentials.get("port")
                stored_hostname = ssh_hostname or ssh_payload.get("host")
            host = ssh_payload.get("host") or cached_host
            username = ssh_payload.get("username") or cached_user
            password = ssh_payload.get("password") or last_used_ssh_credentials.get("password")
            raw_port = ssh_payload.get("port") or cached_port or 22
            try:
                port_value = int(str(raw_port))
            except (TypeError, ValueError):
                port_value = 22

            if not all([host, username, password]):
                yield stream_json_line(
                    {
                        "type": "error",
                        "msg": "Missing SSH credentials (host/username/password).",
                    }
                )
                return False

            client = None
            reuse = False
            if (
                active_client
                and cached_host == host
                and cached_user == username
                and (cached_port or port_value) == port_value
            ):
                client = active_client
                hostname = stored_hostname or host
                reuse = True
                with connection_lock:
                    current_mode = "ssh"
            else:
                _close_serial_connection()
                _close_ssh_connection()
                yield stream_json_line(
                    {
                        "type": "progress",
                        "msg": f"Connecting to {host} via SSH...",
                        "progress_pct": 0,
                    }
                )
                result = _acquire_ssh_connection(host, username, password, port_value)
                client, shell, resolved_port = result
                if not client:
                    yield stream_json_line({"type": "error", "msg": "SSH connection failed."})
                    return False
                try:
                    yield stream_json_line(
                        {
                            "type": "progress",
                            "msg": "Entering enable mode...",
                            "progress_pct": 0,
                        }
                    )
                    enter_enable_mode_remote(client)
                    yield stream_json_line(
                        {
                            "type": "progress",
                            "msg": "Disabling paging...",
                            "progress_pct": 0,
                        }
                    )
                    disable_paging_remote(client)
                    try:
                        hostname = get_hostname_remote(client) or host
                    except Exception:
                        hostname = host
                except Exception as exc:
                    try:
                        if client:
                            existing_shell = getattr(client, "_shell", None)
                            if existing_shell:
                                existing_shell.close()
                            client.close()
                    except Exception:
                        pass
                    yield stream_json_line(
                        {
                            "type": "error",
                            "msg": f"SSH initialization failed: {exc}",
                        }
                    )
                    return False
                _update_ssh_state(client, host, username, password, hostname, resolved_port)

            if not reuse:
                yield stream_json_line(
                    {
                        "type": "progress",
                        "msg": f"Connected to {hostname} via SSH.",
                        "progress_pct": 0,
                    }
                )

            active = client or ssh_client
            if not active:
                yield stream_json_line(
                    {"type": "error", "msg": "SSH connection unavailable after setup."}
                )
                return False

            completed = 0
            total_commands = len(commands)
            for cmd in commands:
                yield stream_json_line({"type": "progress", "msg": f"Running '{cmd}'..."})
                try:
                    output = send_command_remote(active, cmd, timeout=30)
                    file_path = save_output_to_file(
                        cmd,
                        output,
                        exam_name,
                        student_id,
                        session_id,
                        target_device or hostname,
                        base_dir=base_path,
                    )
                    _save_output_to_engine_students(
                        cmd,
                        output,
                        exam_name,
                        session_id,
                        student_id,
                        target_device or hostname,
                    )
                    files_written.append(file_path)
                    completed += 1
                    pct = round((completed / total_commands) * 100) if total_commands else 100
                    yield stream_json_line(
                        {
                            "type": "progress",
                            "msg": f"Completed '{cmd}'.",
                            "cmd_done": True,
                            "progress_pct": pct,
                        }
                    )
                except Exception as exc:
                    del_partial_logs(base_path, exam_name, session_id, student_id, hostname)
                    yield stream_json_line(
                        {
                            "type": "error",
                            "msg": f"Command '{cmd}' failed: {exc}",
                        }
                    )
                    return False

            if not skip_config:
                # Build parsed config.json for the student device logs.
                try:
                    host_folder = target_device or hostname or "device"
                    host_dir = os.path.join(base_path, host_folder)
                    os.makedirs(host_dir, exist_ok=True)
                    config = parse_device_logs(files_written)
                    config_path = os.path.join(host_dir, "config.json")
                    with open(config_path, "w") as handle:
                        json.dump(config, handle, indent=4)
                    yield stream_json_line(
                        {"type": "result", "msg": f"Saved config.json to {config_path}"}
                    )
                except Exception as exc:
                    try:
                        host_folder = target_device or hostname or "device"
                        host_dir = os.path.join(base_path, host_folder)
                        os.makedirs(host_dir, exist_ok=True)
                        fallback = parse_device_logs([])
                        fallback["parse_error"] = str(exc)
                        config_path = os.path.join(host_dir, "config.json")
                        with open(config_path, "w") as handle:
                            json.dump(fallback, handle, indent=4)
                        yield stream_json_line(
                            {
                                "type": "error",
                                "msg": f"Failed to parse logs ({exc}). Wrote fallback config.json to {config_path}",
                            }
                        )
                    except Exception as exc2:
                        yield stream_json_line(
                            {
                                "type": "error",
                                "msg": f"Failed to save config.json: {exc}; fallback failed: {exc2}",
                            }
                        )
            return True

        yield stream_json_line(
            {
                "type": "progress",
                "msg": "Starting execution workflow...",
                "progress_pct": 0,
            }
        )

        try:
            if requested_mode == "serial":
                if not (yield from run_serial()):
                    return
            else:
                if not (yield from run_ssh()):
                    return

            yield stream_json_line(
                {
                    "type": "result",
                    "msg": "All commands executed successfully.",
                    "files": files_written,
                    "progress_pct": 100,
                }
            )
            yield stream_json_line(
                {
                    "type": "done",
                    "msg": "Execution complete.",
                    "progress_pct": 100,
                }
            )
        except Exception as exc:
            tb = traceback.format_exc()
            if hostname:
                del_partial_logs(base_path, exam_name, session_id, student_id, hostname)
            yield stream_json_line({"type": "error", "msg": str(exc), "trace": tb})

    return Response(generate(), mimetype="text/plain")



# -------------------------------------------------
# ✅ Grading System Endpoints
# -------------------------------------------------

def _get_yaml_file(directory, file_id):
    path = directory / f"{file_id}.yaml"
    if not path.exists():
        return None
    try:
        with open(path, "r") as f:
            return yaml.safe_load(f)
    except Exception:
        return None

def _save_yaml_file(directory, file_id, data):
    path = directory / f"{file_id}.yaml"
    with open(path, "w") as f:
        yaml.dump(data, f)
    return str(path)

def _delete_yaml_file(directory, file_id):
    path = directory / f"{file_id}.yaml"
    if path.exists():
        path.unlink()
        return True
    return False

def _list_yaml_files(directory):
    items = []
    if not directory.exists():
        return items
    for f in directory.glob("*.yaml"):
        try:
            with open(f, "r") as yf:
                data = yaml.safe_load(yf) or {}
                # Ensure ID is present
                if "id" not in data:
                    data["id"] = f.stem
                items.append(data)
        except Exception:
            continue
    return sorted(items, key=lambda x: x.get("name", ""))

# --- Schemes ---

@app.route("/api/schemes", methods=["GET"])
def api_list_schemes():
    return jsonify({"status": "ok", "schemes": _list_yaml_files(SCHEMES_DIR)})

@app.route("/api/schemes", methods=["POST"])
def api_save_scheme():
    data = request.get_json() or {}
    scheme_id = data.get("id")
    if not scheme_id:
        import uuid
        scheme_id = str(uuid.uuid4())[:8]
        data["id"] = scheme_id
    
    try:
        _save_yaml_file(SCHEMES_DIR, scheme_id, data)
        return jsonify({"status": "ok", "message": "Scheme saved", "id": scheme_id})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/schemes/<scheme_id>", methods=["DELETE"])
def api_delete_scheme(scheme_id):
    if _delete_yaml_file(SCHEMES_DIR, scheme_id):
        return jsonify({"status": "ok", "message": "Scheme deleted"})
    return jsonify({"status": "error", "message": "Scheme not found"}), 404

# --- Rubrics ---

@app.route("/api/rubrics", methods=["GET"])
def api_list_rubrics():
    return jsonify({"status": "ok", "rubrics": _list_yaml_files(RUBRICS_DIR)})

@app.route("/api/rubrics", methods=["POST"])
def api_save_rubric():
    data = request.get_json() or {}
    rubric_id = data.get("id")
    if not rubric_id:
        import uuid
        rubric_id = str(uuid.uuid4())[:8]
        data["id"] = rubric_id
        
    try:
        _save_yaml_file(RUBRICS_DIR, rubric_id, data)
        return jsonify({"status": "ok", "message": "Rubric saved", "id": rubric_id})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/rubrics/<rubric_id>", methods=["DELETE"])
def api_delete_rubric(rubric_id):
    if _delete_yaml_file(RUBRICS_DIR, rubric_id):
        return jsonify({"status": "ok", "message": "Rubric deleted"})
    return jsonify({"status": "error", "message": "Rubric not found"}), 404

# --- Grading Policy ---

@app.route("/api/grading_policy", methods=["GET"])
def api_get_grading_policy():
    return jsonify({"status": "ok", "policy": load_grading_policy()})


@app.route("/api/grading_policy", methods=["POST"])
def api_save_grading_policy():
    data = request.get_json() or {}
    policy = load_grading_policy()

    major_threshold = data.get("major_threshold", policy.get("major_threshold"))
    minor_threshold = data.get("minor_threshold", policy.get("minor_threshold"))

    try:
        major_threshold = int(major_threshold)
        minor_threshold = int(minor_threshold)
    except Exception:
        return jsonify({"status": "error", "message": "Thresholds must be integers."}), 400

    if major_threshold < 1 or minor_threshold < 1:
        return jsonify(
            {"status": "error", "message": "Thresholds must be at least 1."}
        ), 400

    policy = save_grading_policy(
        {
            "major_threshold": major_threshold,
            "minor_threshold": minor_threshold,
        }
    )
    return jsonify({"status": "ok", "policy": policy})


@app.route("/api/rubric_rules", methods=["GET"])
def api_get_rubric_rules():
    return jsonify({"status": "ok", "rules": load_rubric_rules()})


@app.route("/api/rubric_rules", methods=["POST"])
def api_save_rubric_rules():
    data = request.get_json() or {}
    rules = data.get("rules")
    if rules is None:
        return jsonify({"status": "error", "message": "Missing rules."}), 400
    try:
        saved = save_rubric_rules(rules)
        return jsonify({"status": "ok", "rules": saved})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400


# --- Results View ---

@app.route("/api/results", methods=["GET"])
def api_get_results():
    target_path = request.args.get("target_path")
    if not target_path:
        return jsonify({"status": "error", "message": "Missing target_path."}), 400
    if not os.path.isdir(target_path):
        return jsonify({"status": "error", "message": "target_path not found."}), 404

    return jsonify(
        {
            "status": "ok",
            "reports": _build_session_reports(target_path),
            "policy": load_grading_policy(),
        }
    )


# -------------------------------------------------
# ✅ Admin Cleanup
# -------------------------------------------------
@app.route("/api/admin/templates", methods=["GET"])
def api_admin_list_templates():
    templates = []
    if TEMPLATES_DIR.is_dir():
        for entry in sorted(TEMPLATES_DIR.iterdir()):
            if entry.is_dir():
                templates.append(entry.name)
    return jsonify({"status": "ok", "templates": templates})


@app.route("/api/templates/<template_name>", methods=["GET"])
def api_get_template_details(template_name):
    if not template_name:
        return jsonify({"status": "error", "message": "Missing template name."}), 400

    target = _safe_resolve_child(TEMPLATES_DIR, TEMPLATES_DIR / template_name)
    if not target or not target.exists():
        return jsonify({"status": "error", "message": "Template not found."}), 404

    devices_meta = {}
    logs_by_command = {}
    for hostname_dir in sorted(target.iterdir()):
        if not hostname_dir.is_dir():
            continue
        logs_manifest = hostname_dir / "logs.json"
        commands = []
        if logs_manifest.exists():
            try:
                with open(logs_manifest, "r") as handle:
                    manifest = json.load(handle) or {}
                for name in manifest.get("logs", []):
                    base = os.path.splitext(name)[0]
                    cmd = base.replace("_", " ")
                    commands.append(cmd)
                    logs_by_command.setdefault(hostname_dir.name, {})[cmd] = name
            except Exception:
                commands = []
        if commands:
            devices_meta[hostname_dir.name] = commands

    return jsonify({
        "status": "ok",
        "template": template_name,
        "devices_meta": devices_meta,
        "logs_by_command": logs_by_command,
    })


@app.route("/api/admin/templates", methods=["DELETE"])
def api_admin_delete_templates():
    data = request.get_json() or {}
    name = data.get("name")
    delete_all = bool(data.get("all"))

    if delete_all:
        for entry in TEMPLATES_DIR.iterdir():
            if entry.is_dir():
                try:
                    shutil.rmtree(entry)
                except Exception:
                    pass
        return jsonify({"status": "ok", "message": "All templates deleted"})

    if not name:
        return jsonify({"status": "error", "message": "Missing template name."}), 400

    target = _safe_resolve_child(TEMPLATES_DIR, TEMPLATES_DIR / name)
    if not target or not target.exists():
        return jsonify({"status": "error", "message": "Template not found."}), 404

    shutil.rmtree(target)
    return jsonify({"status": "ok", "message": f"Template '{name}' deleted"})


@app.route("/api/admin/results", methods=["GET"])
def api_admin_list_results():
    results = []
    docs_path = Path.home() / "Documents"
    if docs_path.exists():
        for exam_dir in docs_path.iterdir():
            if not exam_dir.is_dir() or exam_dir.name.startswith("."):
                continue
            for session_dir in exam_dir.iterdir():
                if not session_dir.is_dir():
                    continue
                for student_dir in session_dir.iterdir():
                    if not student_dir.is_dir():
                        continue
                    results_dir = student_dir / "results"
                    if results_dir.is_dir():
                        results.append(
                            {
                                "path": str(results_dir),
                                "exam_name": exam_dir.name,
                                "session_id": session_dir.name,
                                "student_id": student_dir.name,
                                "display": f"{exam_dir.name}/{session_dir.name}/{student_dir.name}",
                            }
                        )
    return jsonify({"status": "ok", "results": results})


@app.route("/api/admin/results", methods=["DELETE"])
def api_admin_delete_results():
    data = request.get_json() or {}
    path = data.get("path")
    delete_all = bool(data.get("all"))

    if delete_all:
        docs_dir = (Path.home() / "Documents").resolve()
        deleted = 0
        if docs_dir.exists():
            for exam_dir in docs_dir.iterdir():
                if not exam_dir.is_dir() or exam_dir.name.startswith("."):
                    continue
                for session_dir in exam_dir.iterdir():
                    if not session_dir.is_dir():
                        continue
                    for student_dir in session_dir.iterdir():
                        if not student_dir.is_dir():
                            continue
                        results_dir = student_dir / "results"
                        if results_dir.is_dir():
                            try:
                                shutil.rmtree(results_dir)
                                deleted += 1
                            except Exception:
                                pass
        return jsonify({"status": "ok", "message": f"All results deleted ({deleted})."})

    if not path:
        return jsonify({"status": "error", "message": "Missing path."}), 400

    docs_dir = (Path.home() / "Documents").resolve()
    target = _safe_resolve_child(docs_dir, Path(path))
    if not target or not target.exists():
        return jsonify({"status": "error", "message": "Result not found."}), 404

    shutil.rmtree(target)
    return jsonify({"status": "ok", "message": f"Results deleted: {target}"})


@app.route("/api/admin/students", methods=["GET"])
def api_admin_list_students():
    return jsonify(
        {
            "status": "ok",
            "students": _list_existing_directories(),
            "sessions": _list_existing_sessions(),
        }
    )


@app.route("/api/admin/students", methods=["DELETE"])
def api_admin_delete_students():
    data = request.get_json() or {}
    path = data.get("path")
    if not path:
        return jsonify({"status": "error", "message": "Missing path."}), 400

    target = _safe_resolve_child(DOCS_DIR, Path(path))
    if not target or not target.exists():
        return jsonify({"status": "error", "message": "Path not found."}), 404

    if target == DOCS_DIR:
        return jsonify(
            {"status": "error", "message": "Refusing to delete Documents root."}
        ), 400

    shutil.rmtree(target)
    _delete_engine_student_logs_for_docs_target(target)
    return jsonify({"status": "ok", "message": f"Deleted {target}"})


@app.route("/api/add_student", methods=["POST"])
def api_add_student():
    data = request.get_json() or {}
    session_path = _expand_path(data.get("session_path"))
    student_id = (data.get("student_id") or "").strip()

    if not session_path or not student_id:
        return jsonify({"status": "error", "message": "Missing session_path or student_id."}), 400

    session_dir = Path(session_path)
    if not session_dir.exists() or not session_dir.is_dir():
        return jsonify({"status": "error", "message": "Session path not found."}), 404

    docs_dir = (Path.home() / "Documents").resolve()
    target = _safe_resolve_child(docs_dir, session_dir)
    if not target:
        return jsonify({"status": "error", "message": "Invalid session path."}), 400

    student_dir = session_dir / student_id
    student_dir.mkdir(parents=True, exist_ok=True)

    parts = student_dir.parts
    exam_name = parts[-3] if len(parts) >= 3 else ""
    session_id = parts[-2] if len(parts) >= 2 else ""
    return jsonify(
        {
            "status": "ok",
            "message": f"Student directory created: {student_dir}",
            "path": str(student_dir),
            "exam_name": exam_name,
            "session_id": session_id,
            "student_id": student_id,
        }
    )

# --- Grading Logic ---

def _substitute_variables(pattern, variables):
    """
    Replace {{key}} in pattern with value from variables.
    """
    for key, val in variables.items():
        # strict replacement of {{key}}
        pattern = pattern.replace(f"{{{{{key}}}}}", str(val))
    return pattern

def _check_criteria(content, criteria, variables):
    """
    Check if content matches the criteria pattern.
    """
    pattern = criteria.get("pattern", "")
    # Substitute variables
    final_pattern = _substitute_variables(pattern, variables)
    
    # Try Regex search
    try:
        if re.search(final_pattern, content, re.MULTILINE | re.IGNORECASE):
            return True, final_pattern
    except re.error:
        pass
        
    # Check for simple string inclusion if regex fails or is simple
    if final_pattern in content:
        return True, final_pattern
        
    return False, final_pattern
from comparsion_engine.compare_main import grading_pipeline

def _load_template_configs(template_name: str):
    template_dir = _safe_resolve_child(TEMPLATES_DIR, TEMPLATES_DIR / template_name)
    if not template_dir or not template_dir.is_dir():
        return None

    template_configs = {}
    for host_dir in sorted(template_dir.iterdir()):
        if not host_dir.is_dir():
            continue
        config_path = host_dir / "config.json"
        if not config_path.exists():
            continue
        try:
            with open(config_path, "r") as handle:
                data = json.load(handle) or {}
            template_configs[host_dir.name] = normalize_parsed_config(data)
        except Exception:
            continue

    return template_configs


def _grade_session_from_config(target_path: str, template_name: str):
    template_configs = _load_template_configs(template_name)
    if not template_configs:
        return [], f"No template configs found for '{template_name}'."

    results_summary = []
    target = Path(target_path)
    if not target.is_dir():
        return [], f"Target path {target_path} not found."

    for student_entry in sorted(target.iterdir()):
        if not student_entry.is_dir():
            continue
        student_id = student_entry.name
        student_results_dir_student = student_entry / "results"
        student_results_dir_student.mkdir(parents=True, exist_ok=True)

        summary = {
            "student_id": student_id,
            "template_name": template_name,
            "grading_mode": "strict",
            "hostnames_compared": [],
            "hostnames_missing_template": [],
            "hostnames_missing_show_run": [],
            "results": {},
        }

        for hostname, template_config in template_configs.items():
            template_config = normalize_parsed_config(template_config)
            student_host_dir = student_entry / hostname
            student_config_path = student_host_dir / "config.json"
            student_config = {}
            if student_config_path.exists():
                try:
                    with open(student_config_path, "r") as handle:
                        student_config = json.load(handle) or {}
                except Exception:
                    student_config = {}
            student_config = normalize_parsed_config(student_config)

            show_run_file = None
            if student_host_dir.is_dir():
                show_run_file = find_show_run_file(str(student_host_dir))
            if not show_run_file:
                summary["hostnames_missing_show_run"].append(hostname)

            results = compare_dicts(template_config, student_config)
            summary["hostnames_compared"].append(hostname)
            summary["results"][hostname] = results

            parsed_file = student_results_dir_student / f"{hostname}_student_parsed.json"
            with open(parsed_file, "w") as handle:
                json.dump(student_config, handle, indent=4)

            result_payload = {
                "student_id": student_id,
                "template_name": template_name,
                "grading_mode": "strict",
                "hostname": hostname,
                "student_show_run_file": show_run_file,
                "student_config_file": str(student_config_path) if student_config_path.exists() else None,
                "student_parsed_file": str(parsed_file),
                "results": results,
            }

            student_result_file = student_results_dir_student / f"{hostname}_result.json"
            with open(student_result_file, "w") as handle:
                json.dump(result_payload, handle, indent=4)

        summary_file_student = student_results_dir_student / "summary.json"
        with open(summary_file_student, "w") as handle:
            json.dump(summary, handle, indent=4)

        results_summary.append(
            {"student_id": student_id, "status": "Graded", "template": template_name}
        )

    return results_summary, "Grading completed."


@app.route("/api/grade", methods=["POST"])
def api_run_grading():
    data = request.get_json() or {}
    exam_name = data.get("exam_name")
    session_id = data.get("session_id")
    target_path = data.get("target_path")
    template_name = data.get("template_name")
    include_reports = bool(data.get("include_reports"))
    
    if not all([exam_name, session_id, target_path]):
         return jsonify({"status": "error", "message": "Missing arguments"}), 400
         
    try:
        # Determine template to use
        available_templates = []
        if TEMPLATES_DIR.is_dir():
            available_templates = [p.name for p in TEMPLATES_DIR.iterdir() if p.is_dir()]

        chosen_template = template_name
        if not chosen_template:
            if len(available_templates) == 1:
                chosen_template = available_templates[0]
            else:
                return jsonify({
                    "status": "error",
                    "message": "Multiple templates available. Please select a template.",
                    "templates": available_templates,
                }), 400

        summary_results, message = _grade_session_from_config(target_path, chosen_template)

        payload = {
            "status": "success",
            "message": message,
            "results": summary_results,
        }
        if include_reports:
            payload["reports"] = _build_session_reports(target_path)
            payload["policy"] = load_grading_policy()
        
        return jsonify(payload)
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"status": "error", "message": f"Grading failed: {str(e)}"}), 500

# -------------------------------------------------
# ✅ Template Upload
# -------------------------------------------------
@app.route("/api/templates/upload", methods=["POST"])
def api_upload_templates():
    """
    Handles form-data upload from device_setup.html.
    Creates template config.json using parsing logic.
    """
    from comparison_wrapper import handle_template_upload
    
    form_data = request.form
    files = request.files

    print(f"\n[API][templates/upload] Uploading new template...")
    # Base dir for templates
    base_dir = os.path.dirname(os.path.abspath(__file__))
    
    try:
        results = handle_template_upload(files, form_data, base_dir)
        if results.get("status") == "error":
            return jsonify(results), 400
        print(f"[API][templates/upload] Extraction successful: {results}")
        return jsonify({"status": "success", "results": results})
    except Exception as e:
        print(f"[API][templates/upload] Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


# -------------------------------------------------
# ✅ Run Flask
# -------------------------------------------------
if __name__ == "__main__":
    print("[*] Running Flask server on http://127.0.0.1:5050")
    app.run(host="127.0.0.1", port=5050, threaded=True)
