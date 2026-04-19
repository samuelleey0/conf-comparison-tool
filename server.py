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
import logging

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
from comparison_engine.parser import parse_device_logs, normalize_parsed_config
from comparison_engine.comparator import compare_dicts
from comparison_engine.student_manager import find_show_run_file
from cisco_reset import reload_cisco_device

app = Flask(__name__)

# Base directory for consistent absolute paths
BASE_DIR = Path(__file__).resolve().parent

# Grading Directories
SCHEMES_DIR = BASE_DIR / "schemes"
RUBRICS_DIR = BASE_DIR / "rubrics"
TEMPLATES_DIR = BASE_DIR / "comparison_engine" / "templates"
ENGINE_STUDENTS_DIR = BASE_DIR / "comparison_engine" / "students"
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
last_used_ssh_credentials = {
    "host": None,
    "username": None,
    "password": None,
    "port": 22,
}

execution_abort = threading.Event()


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
        (
            "MISMATCH_ROUTING_PROTOCOL",
            "major",
            "Wrong routing protocol type configured.",
        ),
        (
            "MISMATCH_IRP_INCONSEQUENTIAL",
            "minor",
            "Non-functioning routing protocol configured.",
        ),
        (
            "MISMATCH_ROUTING_NETWORK",
            "major",
            "Routing protocol network statement differs.",
        ),
        (
            "EXTRA_NETWORK_ADVERTISED",
            "minor",
            "Network advertised when it should not be.",
        ),
        (
            "MISMATCH_AUTO_SUMMARY",
            "minor",
            "Auto-summarisation not disabled when required.",
        ),
        (
            "MISMATCH_AUTO_SUMMARY_INCONSEQUENTIAL",
            "minor",
            "Auto-summarisation not disabled (inconsequential).",
        ),
        (
            "EXTRA_REDISTRIBUTE",
            "minor",
            "Default routes redistributed when they should not be.",
        ),
        (
            "MISSING_REDISTRIBUTE",
            "major",
            "Default routes not redistributed when required.",
        ),
        (
            "MISSING_NETWORK_STATEMENT_INTERFACE",
            "major",
            "Routing protocol network statement matches no interface.",
        ),
        (
            "EXTRA_ROUTING_INSTANCE",
            "major",
            "Multiple routing protocol instances configured.",
        ),
        ("MISMATCH_RIP_VERSION", "minor", "RIP version incorrectly set."),
        (
            "MISMATCH_EIGRP_AS_MISMATCH",
            "major",
            "EIGRP AS numbers not common across devices.",
        ),
        ("MISMATCH_EIGRP_AS_INCORRECT", "major", "EIGRP AS number incorrectly set."),
        ("MISMATCH_ROUTING_PASSIVE", "minor", "Passive interface declaration differs."),
        ("MISMATCH_ROUTING_REDISTRIBUTE", "minor", "Redistribution statement differs."),
        ("MISMATCH_ROUTING_AUTO_SUMMARY", "minor", "Auto-summary setting differs."),
        ("MISMATCH_OSPF_AREA_ID_SINGLE", "major", "Single-area OSPF not using area 0."),
        ("MISMATCH_OSPF_AREA_0_MISSING", "major", "Multi-area OSPF missing area 0."),
        (
            "MISMATCH_OSPF_AREA_LINK",
            "major",
            "Interfaces on same link in different OSPF areas.",
        ),
        (
            "MISMATCH_OSPF_NOT_MULTIAREA",
            "major",
            "OSPF is not multi-area when required.",
        ),
        (
            "MISMATCH_OSPF_NOT_SINGLEAREA",
            "major",
            "OSPF is not single-area when required.",
        ),
        (
            "MISMATCH_OSPF_GROUP_MULTIAREA",
            "major",
            "OSPF network groups not in a single area.",
        ),
        ("MISMATCH_OSPF_WRONG_AREA", "major", "OSPF network groups in wrong area."),
        (
            "MISSING_OSPF_POINT_TO_POINT",
            "minor",
            "Missing ip ospf network point-to-point command.",
        ),
        ("MISMATCH_STATIC_ROUTE_NETWORK", "major", "Static route destination differs."),
        ("MISMATCH_STATIC_ROUTE_NEXTHOP", "minor", "Static route next-hop differs."),
        ("MISMATCH_DEFAULT_ROUTE_NEXTHOP", "major", "Default route next-hop differs."),
        ("EXTRA_STATIC_ROUTE", "minor", "Extra static route added when not required."),
        ("EXTRA_DEFAULT_ROUTE", "minor", "Multiple correct default routes configured."),
        (
            "EXTRA_STATIC_CONFIGURED",
            "minor",
            "Static routes installed when there should be none.",
        ),
        ("MISMATCH_DHCP_POOL", "major", "DHCP pool properties incorrect."),
        ("MISMATCH_DHCP_EXCLUDED", "minor", "DHCP excluded range differs."),
        ("MISMATCH_DHCP_BAD_EXCLUSION", "minor", "Excluded IPs outside pool range."),
        ("EXTRA_DHCP_POOL", "major", "DHCP configured when it should not be."),
        (
            "EXTRA_DHCP_ADVERTISING",
            "minor",
            "DHCP pools advertising when should not be.",
        ),
        ("MISMATCH_NAT_DIRECTION", "major", "NAT inside/outside assignment differs."),
        ("MISMATCH_NAT_POOL", "major", "NAT pool range differs."),
        (
            "MISMATCH_NAT_POOL_RANGE_OUTSIDE",
            "major",
            "NAT pool range extends outside required range.",
        ),
        (
            "MISMATCH_NAT_POOL_RANGE_INSIDE",
            "major",
            "NAT pool range does not cover required range.",
        ),
        ("MISMATCH_NAT_POOL_PREFIX", "major", "NAT pool prefix incorrect."),
        ("MISMATCH_NAT_ACL_BINDING", "major", "NAT ACL to pool binding differs."),
        ("MISMATCH_NAT_ACL_INCORRECT", "major", "NAT ACL does not match required IPs."),
        ("MISMATCH_NAT_ACL_NOT_OPTIMAL", "minor", "NAT ACL not using optimal set."),
        (
            "MISMATCH_NAT_ACL_POINTLESS_NORMAL",
            "minor",
            "Pointless NAT ACL rules shadowed by earlier rules.",
        ),
        (
            "MISMATCH_NAT_ACL_POINTLESS_DEFAULT",
            "minor",
            "Pointless NAT ACL rules after permit/deny any any.",
        ),
        ("EXTRA_NAT_OVERLOAD", "major", "NAT pool overloaded when it should not be."),
        (
            "EXTRA_NAT_INTERFACE_CONFIGURED",
            "major",
            "NAT configured on interface when it should not be.",
        ),
        ("MISMATCH_IP", "major", "Interface IP address differs from template."),
        ("MISMATCH_MASK", "major", "Interface subnet mask differs."),
        ("MISMATCH_GATEWAY_ADDRESS", "major", "Incorrect IP on gateway interface."),
        ("MISMATCH_HOST_ADDRESS", "minor", "Incorrect IP on host interface."),
        (
            "MISMATCH_TRUNK_IP_ASSIGNED",
            "major",
            "Trunk interface has IP when it should have sub-interfaces.",
        ),
        (
            "MISMATCH_SUB_INT_VLAN",
            "major",
            "Sub-interface name does not match VLAN ID.",
        ),
        ("MISMATCH_LINK_NETWORK", "major", "Shared L3 link not in same network."),
        (
            "MISMATCH_LINK_CONFLICT_ADDRESS",
            "major",
            "Shared L3 link has conflicting IPs.",
        ),
        ("MISMATCH_SHUTDOWN", "major", "Interface shutdown state differs."),
        (
            "MISMATCH_CONFIGURED_IF_SHUTDOWN",
            "minor",
            "Interface shutdown but configured.",
        ),
        ("MISMATCH_CLOCK_RATE", "minor", "Serial clock rate differs."),
        ("MISSING_DESCRIPTION", "minor", "Required interface description not set."),
        (
            "EXTRA_DESCRIPTION",
            "minor",
            "Interface description set when it should not be.",
        ),
        (
            "EXTRA_IFACE_CONFIGURED",
            "minor",
            "Interface configured when it should not have been.",
        ),
        ("MISMATCH_ENCAPSULATION", "major", "Serial encapsulation type differs."),
        ("EXTRA_PPP_ENABLED", "major", "PPP enabled when it should not be."),
        ("MISMATCH_PPP_AUTH", "major", "PPP authentication method differs."),
        (
            "MISMATCH_PPP_PROTOCOL_CONFLICT",
            "major",
            "PPP protocol conflict on shared link.",
        ),
        ("MISMATCH_PPP_USERNAME", "major", "Incorrect PPP username."),
        ("MISMATCH_PPP_PASSWORD", "major", "Incorrect PPP password."),
        ("MISSING_PPP_ACCOUNT", "major", "Required PPP account missing."),
        ("MISMATCH_PPP_ACCOUNT_PASSWORD", "major", "PPP account has invalid password."),
        ("MISMATCH_ACL_TYPE", "major", "ACL type differs."),
        ("MISMATCH_ACL_RULE", "major", "ACL rule content differs."),
        ("MISMATCH_ACL_NOT_OPTIMAL", "minor", "ACL not using optimal rules."),
        (
            "MISMATCH_ACL_POINTLESS_NORMAL",
            "minor",
            "ACL rules shadowed by earlier rules.",
        ),
        (
            "MISMATCH_ACL_POINTLESS_DEFAULT",
            "minor",
            "ACL rules after permit/deny any any.",
        ),
        (
            "MISMATCH_ACL_UNCLEAN_PRUNE",
            "minor",
            "ACL rule ranges outside equivalent of any.",
        ),
        (
            "EXTRA_ACL_APPLIED",
            "major",
            "ACL applied to interface when it should not be.",
        ),
        ("MISSING_ACL_APPLIED", "major", "ACL configured but not applied."),
        ("MISMATCH_SWITCHPORT_MODE", "major", "Switchport mode differs."),
        ("MISMATCH_ACCESS_VLAN", "major", "Access VLAN number differs."),
        (
            "MISMATCH_VLAN_INTERFACE",
            "major",
            "VLAN SVI numbering differs from template.",
        ),
        ("MISMATCH_VLAN_ID", "major", "VLAN ID differs."),
        ("MISMATCH_VLAN_NAME", "minor", "VLAN name differs."),
        ("MISMATCH_TRUNK_ENCAPSULATION", "major", "Trunk encapsulation differs."),
        ("MISMATCH_TRUNK_MODE", "major", "Trunk mode differs."),
        ("MISMATCH_TRUNK_NATIVE_VLAN", "major", "Trunk native VLAN differs."),
        ("MISMATCH_TRUNK_ALLOWED_VLANS", "major", "Allowed VLAN list differs."),
        ("MISMATCH_TRUNK_BOTH_ENDS", "major", "Trunk not trunking at both ends."),
        ("MISMATCH_TRUNK_ENCAP_MISMATCH", "major", "Trunk encapsulation mismatch."),
        (
            "MISMATCH_TRUNK_NATIVE_VLAN_MISMATCH",
            "major",
            "Native VLAN mismatch between switches.",
        ),
        ("MISMATCH_PORT_SECURITY_MAX", "minor", "Port security maximum differs."),
        (
            "MISMATCH_PORT_SECURITY_VIOLATION",
            "major",
            "Port security violation action differs.",
        ),
        (
            "MISMATCH_PORT_SECURITY_STICKY",
            "minor",
            "Port security sticky setting differs.",
        ),
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
        (
            "MISMATCH_ETHERCHANNEL_LOAD_BALANCE",
            "minor",
            "EtherChannel load-balancing differs.",
        ),
        (
            "MISMATCH_PORTCHANNEL_VLAN",
            "major",
            "VLANs allowed on port-channel trunk differ.",
        ),
        (
            "MISMATCH_PORTCHANNEL_TRUNK_MODE",
            "major",
            "Port-channel trunk mode differs.",
        ),
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
        (
            "MISSING_ROUTING_NETWORK",
            "major",
            "Network statement absent from routing protocol.",
        ),
        ("MISSING_STATIC_ROUTE", "major", "Static route absent."),
        ("MISSING_DEFAULT_ROUTE", "major", "Default route not configured."),
        ("MISSING_DEFAULT_GATEWAY", "major", "Switch default gateway not configured."),
        ("MISSING_PASSIVE_INTERFACE", "minor", "Passive interface missing."),
        (
            "MISSING_REDISTRIBUTE",
            "major",
            "Default routes not redistributed when required.",
        ),
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
        (
            "MISSING_PORT_SECURITY",
            "major",
            "Port security not enabled on required port.",
        ),
        ("MISSING_STP_MODE", "minor", "Spanning tree mode not configured."),
        ("MISSING_STP_PRIORITY", "major", "Bridge priority not set on required VLAN."),
        (
            "MISSING_STP_ROOT_FORCED",
            "major",
            "Root bridge not forced to be established.",
        ),
        ("MISSING_STP_PORTFAST", "minor", "PortFast not configured on access port."),
        ("MISSING_STP_BPDU_GUARD", "minor", "BPDU guard not configured."),
        ("MISSING_ETHERCHANNEL", "major", "EtherChannel group absent."),
        (
            "MISSING_ETHERCHANNEL_MEMBER",
            "major",
            "Interface missing from port-channel.",
        ),
        ("MISSING_PORTCHANNEL_TRUNK", "major", "Port-channel not configured as trunk."),
        ("MISSING_PAGP_MODE", "major", "PAgP mode not configured."),
        ("MISSING_LACP_MODE", "major", "LACP mode not configured."),
        ("MISSING_LACP_FAST_TIMER", "minor", "LACP fast timer not configured."),
        ("MISSING_VTY_LOGIN", "minor", "VTY login not configured."),
        ("MISSING_LINE_PASSWORD", "major", "No password specified on lines."),
        ("MISSING_USER", "major", "Required user account absent."),
        (
            "EXTRA_ROUTING_PROTOCOL",
            "major",
            "Routing protocol configured when not required.",
        ),
        ("EXTRA_ROUTING_NETWORK", "minor", "Extra network advertised."),
        (
            "EXTRA_ROUTING_INSTANCE",
            "major",
            "Multiple routing protocol instances configured.",
        ),
        ("EXTRA_STATIC_ROUTE", "minor", "Extra static route added."),
        ("EXTRA_DEFAULT_ROUTE", "minor", "Redundant default routes configured."),
        (
            "EXTRA_STATIC_CONFIGURED",
            "minor",
            "Static routes installed when they should be none.",
        ),
        (
            "EXTRA_REDISTRIBUTE",
            "minor",
            "Default routes redistributed when not required.",
        ),
        ("EXTRA_DHCP_POOL", "major", "Unrequired DHCP pool configured."),
        ("EXTRA_DHCP_ADVERTISING", "minor", "DHCP pool advertising when not required."),
        ("EXTRA_NAT_OVERLOAD", "major", "NAT pool overloaded when it should not be."),
        (
            "EXTRA_NAT_INTERFACE_CONFIGURED",
            "major",
            "NAT configured on interface when not required.",
        ),
        ("EXTRA_NAT_SOURCE", "minor", "Extra NAT inside source binding."),
        (
            "EXTRA_IFACE_CONFIGURED",
            "minor",
            "Interface configured when it should not have been.",
        ),
        ("EXTRA_PPP_ENABLED", "major", "PPP enabled when it should not be."),
        ("EXTRA_ACL", "minor", "Extra ACL created."),
        ("EXTRA_ACL_RULE", "minor", "Extra ACL rule added."),
        ("EXTRA_ACL_APPLIED", "major", "ACL applied to wrong interface."),
        ("EXTRA_VLAN", "minor", "Extra VLAN created."),
        ("EXTRA_VTY_CONFIG", "minor", "Extra VTY command added."),
        (
            "EXTRA_DESCRIPTION",
            "minor",
            "Interface description added when not required.",
        ),
        ("EXTRA_USER", "minor", "Extra user account created."),
        ("EXTRA_TRUNK_ALLOWED_VLAN", "minor", "Extra VLAN allowed on trunk."),
        ("EXTRA_STP_PRIORITY", "minor", "Extra STP priority configured."),
        ("EXTRA_ETHERCHANNEL", "minor", "Extra port-channel group created."),
        (
            "EXTRA_ETHERCHANNEL_MEMBER",
            "minor",
            "Extra interface added to port-channel.",
        ),
        ("EXTRA_LINE_ENABLED", "major", "Line access enabled when it should not be."),
        (
            "VERIFY_IFACE_DOWN",
            "minor",
            "Interface should be up but is down in show ip interface brief.",
        ),
        (
            "VERIFY_IFACE_IP_WRONG",
            "major",
            "Interface IP in show ip interface brief differs from template.",
        ),
        (
            "VERIFY_IFACE_ADMIN_UP",
            "major",
            "Interface should be administratively down but is up.",
        ),
        (
            "VERIFY_ROUTE_MISSING_DEFAULT",
            "major",
            "Expected default route not installed in routing table.",
        ),
        (
            "VERIFY_ROUTE_MISSING_LEARNED",
            "major",
            "Expected learned routes missing from routing table.",
        ),
        (
            "VERIFY_GATEWAY_WRONG",
            "major",
            "Gateway of last resort differs from template.",
        ),
        (
            "VERIFY_ROUTE_PROTOCOL_ABSENT",
            "major",
            "Expected route protocol code absent from routing table.",
        ),
        ("VERIFY_ACL_MISSING", "major", "Template ACL absent in show access-lists."),
        (
            "VERIFY_ACL_RULE_WRONG",
            "major",
            "ACL rule content differs in show access-lists.",
        ),
        (
            "VERIFY_ACL_EMPTY",
            "major",
            "ACL exists but has no rules in show access-lists.",
        ),
        (
            "VERIFY_NAT_NOT_WORKING",
            "major",
            "NAT translations not present when template shows NAT tested.",
        ),
        (
            "VERIFY_NAT_IFACE_WRONG",
            "major",
            "NAT inside/outside interfaces differ in show ip nat statistics.",
        ),
        (
            "VERIFY_NAT_MAPPING_WRONG",
            "major",
            "NAT ACL-to-pool mappings differ in show ip nat statistics.",
        ),
        (
            "VERIFY_NAT_POOL_WRONG",
            "major",
            "NAT pool details differ in show ip nat statistics.",
        ),
        (
            "VERIFY_NAT_NO_STATS",
            "major",
            "Template has NAT statistics but student has none.",
        ),
        (
            "VERIFY_DHCP_NOT_ASSIGNING",
            "minor",
            "Template shows DHCP bindings but student has none.",
        ),
        (
            "VERIFY_DHCP_POOL_MISSING",
            "major",
            "Required DHCP pool name absent in show ip dhcp pool.",
        ),
        (
            "VERIFY_DHCP_POOL_EXTRA",
            "minor",
            "Extra DHCP pool name present in show ip dhcp pool.",
        ),
        (
            "VERIFY_VLAN_SCHEME_MISMATCH",
            "major",
            "VLAN scheme differs in show vlan brief.",
        ),
        ("VERIFY_VLAN_MISSING", "major", "Required VLAN missing from show vlan brief."),
        ("VERIFY_VLAN_NAME_WRONG", "minor", "VLAN name differs in show vlan brief."),
        ("VERIFY_VLAN_NOT_ACTIVE", "major", "VLAN exists but is not active."),
        (
            "VERIFY_VLAN_PORT_WRONG",
            "major",
            "VLAN port assignments differ in show vlan brief.",
        ),
        ("VERIFY_VLAN_EXTRA", "minor", "Extra VLAN present in show vlan brief."),
        (
            "VERIFY_TRUNK_NOT_TRUNKING",
            "major",
            "Interface is not trunking in show interfaces trunk.",
        ),
        (
            "VERIFY_TRUNK_ENCAP_WRONG",
            "major",
            "Trunk encapsulation differs in show interfaces trunk.",
        ),
        (
            "VERIFY_TRUNK_NATIVE_WRONG",
            "major",
            "Trunk native VLAN differs in show interfaces trunk.",
        ),
        (
            "VERIFY_TRUNK_MODE_WRONG",
            "minor",
            "Trunk mode differs in show interfaces trunk.",
        ),
        (
            "VERIFY_PORT_SECURITY_MAX_WRONG",
            "minor",
            "Port security maximum differs in show port-security.",
        ),
        (
            "VERIFY_PORT_SECURITY_ACTION_WRONG",
            "major",
            "Port security action differs in show port-security.",
        ),
        (
            "VERIFY_PORT_SECURITY_VIOLATION",
            "minor",
            "Port security violations detected in show port-security.",
        ),
        (
            "VERIFY_PORT_SECURITY_MISSING_IFACE",
            "major",
            "Expected secured interface missing from show port-security.",
        ),
        ("VERIFY_EIGRP_NO_NEIGHBORS", "major", "No EIGRP neighbors formed."),
        (
            "VERIFY_EIGRP_NEIGHBOR_COUNT",
            "major",
            "Fewer EIGRP neighbors than expected.",
        ),
        (
            "VERIFY_EIGRP_WRONG_INTERFACE",
            "major",
            "EIGRP neighbor formed on the wrong interface.",
        ),
        (
            "VERIFY_EIGRP_ROUTE_MISSING",
            "major",
            "Expected EIGRP route missing from topology.",
        ),
        (
            "VERIFY_EIGRP_ROUTE_ACTIVE",
            "minor",
            "EIGRP route is Active instead of Passive.",
        ),
        (
            "VERIFY_EIGRP_IFACE_MISSING",
            "major",
            "Required interface missing from EIGRP interfaces.",
        ),
        (
            "VERIFY_EIGRP_PASSIVE_ACTIVE",
            "minor",
            "Passive interface appears active in EIGRP.",
        ),
        ("VERIFY_OSPF_NO_NEIGHBORS", "major", "No OSPF neighbors formed."),
        ("VERIFY_OSPF_NOT_FULL", "major", "OSPF neighbor not in FULL state."),
        ("VERIFY_OSPF_NEIGHBOR_COUNT", "major", "Fewer OSPF neighbors than expected."),
        (
            "VERIFY_OSPF_WRONG_INTERFACE",
            "major",
            "OSPF neighbor formed on the wrong interface.",
        ),
        (
            "VERIFY_OSPF_NO_ROUTER_LSA",
            "major",
            "No Router LSAs present in OSPF database.",
        ),
        (
            "VERIFY_OSPF_NO_SUMMARY_LSA",
            "major",
            "No Summary LSAs present in OSPF database.",
        ),
        (
            "VERIFY_OSPF_NO_EXTERNAL_LSA",
            "minor",
            "No External LSAs present when redistribution is expected.",
        ),
        ("VERIFY_OSPF_WRONG_AREA", "major", "OSPF interface is in the wrong area."),
        (
            "VERIFY_OSPF_WRONG_NETWORK_TYPE",
            "minor",
            "OSPF network type differs from template.",
        ),
        ("VERIFY_OSPF_IFACE_DOWN", "major", "OSPF interface is down or missing."),
        ("VERIFY_RIP_DATABASE_EMPTY", "major", "RIP database is empty."),
        (
            "VERIFY_RIP_ROUTE_MISSING",
            "major",
            "Expected RIP route missing from database.",
        ),
        ("VERIFY_RIP_ROUTE_UNREACHABLE", "major", "RIP route metric is unreachable."),
        (
            "VERIFY_RIP_ROUTE_POSSIBLY_DOWN",
            "minor",
            "RIP route flagged as possibly down.",
        ),
        (
            "VERIFY_STATIC_ROUTE_MISSING",
            "major",
            "Expected static route not installed.",
        ),
        ("VERIFY_DEFAULT_ROUTE_MISSING", "major", "Default route not installed."),
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
        "MISMATCH_IP": [
            r"\.interfaces\..+\.ip$",
            r"\.show_ip_interface_brief\.interfaces\..+\.ip$",
        ],
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
        "MISMATCH_TRUNK_ENCAPSULATION": [
            r"\.trunk_encapsulation$",
            r"\.show_interfaces_trunk\..*\.encapsulation$",
        ],
        "MISMATCH_TRUNK_MODE": [r"\.show_interfaces_trunk\..*\.mode$"],
        "MISMATCH_TRUNK_NATIVE_VLAN": [
            r"\.trunk_native_vlan$",
            r"\.show_interfaces_trunk\..*\.native_vlan$",
        ],
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
        "MISMATCH_STP_PRIORITY": [
            r"\.spanning_tree\.vlan_priorities",
            r"\.spanning_tree\.vlan_root",
        ],
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
        "MISSING_ETHERCHANNEL_MEMBER": [
            r"\.show_etherchannel_summary\.groups\..+\.members"
        ],
        "EXTRA_ETHERCHANNEL_MEMBER": [
            r"\.show_etherchannel_summary\.groups\..+\.members"
        ],
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
        if code.startswith("VERIFY_"):
            return "4 Verification Layer"
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
        elif prefix == "VERIFY":
            statuses = ["missing", "extra", "mismatch"]
        category = code.split("_", 2)[1].lower() if "_" in code else "rule"
        subcategory = (
            "_".join(code.split("_")[2:]).lower() if code.count("_") >= 2 else ""
        )
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
    default_by_id = {
        rule.get("id"): rule for rule in defaults if isinstance(rule, dict)
    }
    default_ids = set(default_by_id.keys())
    # Tag defaults
    for rule in defaults:
        rule["is_default"] = True
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
            # Separate default rules from custom rules
            saved_defaults = []
            custom_rules = []
            for rule in data:
                if not isinstance(rule, dict):
                    continue
                if rule.get("id") in default_ids:
                    saved_defaults.append(rule)
                else:
                    custom_rules.append(rule)
            # Backfill missing fields for default rules
            for rule in saved_defaults:
                rid = rule.get("id")
                dflt = default_by_id.get(rid, {})
                if not rule.get("section"):
                    rule["section"] = dflt.get("section", "")
                if not rule.get("code"):
                    rule["code"] = dflt.get("code", rid)
                if not rule.get("statuses"):
                    rule["statuses"] = dflt.get("statuses")
                rule["is_default"] = True
            # Merge: saved defaults + new defaults not yet saved + custom rules
            existing = {rule.get("id"): rule for rule in saved_defaults}
            merged = list(saved_defaults)
            for rule in defaults:
                if rule.get("id") not in existing:
                    merged.append(rule)
            # Tag and append custom rules
            for rule in custom_rules:
                rule["is_default"] = False
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
        entry = {
            "id": rid,
            "code": rule.get("code") or rid,
            "category": rule.get("category") or "",
            "subcategory": rule.get("subcategory") or "",
            "section": rule.get("section") or "",
            "description": rule.get("description") or "",
            "severity": (rule.get("severity") or "minor").lower(),
            "enabled": bool(rule.get("enabled", True)),
            "statuses": rule.get("statuses"),
            "patterns": rule.get("patterns") or [],
            "is_default": bool(rule.get("is_default", False)),
        }
        cleaned.append(entry)
    RUBRIC_RULES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RUBRIC_RULES_PATH, "w") as handle:
        json.dump(cleaned, handle, indent=2)
    return cleaned


def reset_rubric_rules():
    """Delete saved rules and return fresh defaults."""
    if RUBRIC_RULES_PATH.exists():
        RUBRIC_RULES_PATH.unlink()
    return load_rubric_rules()


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


def _compile_rubric_rules(rubric_rules=None):
    rubric_compiled = []
    rubric_by_code = {}
    disabled_compiled = []
    disabled_by_code = {}
    for rule in rubric_rules or []:
        code = rule.get("code") or rule.get("id")
        enabled = rule.get("enabled", True)
        if code and enabled:
            rubric_by_code[code] = rule
        elif code and not enabled:
            disabled_by_code[code] = rule
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
        if compiled_patterns and enabled:
            rubric_compiled.append((rule, compiled_patterns, statuses))
        elif compiled_patterns and not enabled:
            disabled_compiled.append((rule, compiled_patterns, statuses))
    return rubric_compiled, rubric_by_code, disabled_compiled, disabled_by_code


def _is_verification_feature(feature: str) -> bool:
    return str(feature or "").startswith("verification.")


def _empty_phase1_summary():
    return {
        "correct": 0,
        "missing": 0,
        "extra": 0,
        "mismatch": 0,
        "major": 0,
        "minor": 0,
        "skipped": 0,
        "config_correct": 0,
        "config_mismatch": 0,
        "config_missing": 0,
        "config_extra": 0,
        "config_skipped": 0,
        "verify_correct": 0,
        "verify_failed": 0,
        "verify_skipped": 0,
        "verify_deduplicated": 0,
    }


def _expand_interface_name(name: str) -> str:
    text = str(name or "").strip()
    full_names = (
        "GigabitEthernet",
        "FastEthernet",
        "Serial",
        "Loopback",
        "Vlan",
        "Port-channel",
        "Tunnel",
    )
    if text.startswith(full_names):
        return text

    replacements = (
        ("Gi", "GigabitEthernet"),
        ("Fa", "FastEthernet"),
        ("Se", "Serial"),
        ("Lo", "Loopback"),
        ("Vl", "Vlan"),
        ("Po", "Port-channel"),
        ("Tu", "Tunnel"),
    )
    for short, long_name in replacements:
        match = re.match(rf"^{re.escape(short)}(?=\d)", text)
        if match:
            return long_name + text[match.end() :]
    return text


def _config_block_ref(feature: str):
    parts = [part for part in str(feature or "").split(".") if part]
    if not parts:
        return None
    if parts[0] != "show_running_config":
        return None

    if len(parts) >= 3 and parts[1] == "interfaces":
        return ".".join(parts[:3])
    if (
        len(parts) >= 5
        and parts[1] == "switching"
        and parts[2] == "etherchannel"
        and parts[3] == "groups"
    ):
        return ".".join(parts[:5])
    if len(parts) >= 3 and parts[1] in {
        "routing",
        "access_lists",
        "users",
        "dhcp_pools",
    }:
        return ".".join(parts[:3])
    if len(parts) >= 3 and parts[1] == "switching":
        return ".".join(parts[:3])
    if len(parts) >= 3 and parts[1] == "vty":
        return ".".join(parts[:3])
    if len(parts) >= 2:
        return ".".join(parts[:2])
    return ".".join(parts)


def _verification_layer1_ref(feature: str):
    parts = [part for part in str(feature or "").split(".") if part]
    if len(parts) < 2 or parts[0] != "verification":
        return None

    routing_command_map = {
        "show_ip_eigrp_neighbor": "show_running_config.routing.eigrp",
        "show_ip_eigrp_topology": "show_running_config.routing.eigrp",
        "show_ip_eigrp_interfaces": "show_running_config.routing.eigrp",
        "show_ip_ospf_neighbor": "show_running_config.routing.ospf",
        "show_ip_ospf_database": "show_running_config.routing.ospf",
        "show_ip_ospf_interface": "show_running_config.routing.ospf",
        "show_ip_rip_database": "show_running_config.routing.rip",
        "show_ip_route_static": "show_running_config.routing.static_routes",
    }
    if parts[1] in routing_command_map:
        return routing_command_map[parts[1]]

    if (
        len(parts) >= 4
        and parts[1] == "show_ip_interface_brief"
        and parts[2] == "interfaces"
    ):
        return f"show_running_config.interfaces.{_expand_interface_name(parts[3])}"

    if len(parts) >= 4 and parts[1] == "show_interfaces_trunk" and parts[2] == "trunks":
        return f"show_running_config.interfaces.{_expand_interface_name(parts[3])}"

    if (
        len(parts) >= 4
        and parts[1] == "show_port_security"
        and parts[2] == "interfaces"
    ):
        return f"show_running_config.interfaces.{_expand_interface_name(parts[3])}"

    if len(parts) >= 4 and parts[1] == "show_access_lists" and parts[2] == "acls":
        return f"show_running_config.access_lists.{parts[3]}"

    if (
        len(parts) >= 4
        and parts[1] == "show_etherchannel_summary"
        and parts[2] == "groups"
    ):
        return f"show_running_config.switching.etherchannel.groups.{parts[3]}"

    if parts[1] in {"show_ip_nat_statistics", "show_ip_nat_translations"}:
        return "show_running_config.nat"

    if parts[1] in {"show_ip_dhcp_binding", "show_ip_dhcp_pool"}:
        return "show_running_config.dhcp_pools"

    if parts[1] == "show_ip_route":
        if len(parts) >= 3 and parts[2] in {"eigrp", "ospf", "rip"}:
            return f"show_running_config.routing.{parts[2]}"
        if len(parts) >= 3 and parts[2] == "static":
            return "show_running_config.routing.static_routes"
        return "show_running_config.routing"

    if parts[1] == "show_vlan_brief":
        return "show_running_config.__vlan_scheme__"

    return None


def _verification_block_name(feature: str):
    parts = [part for part in str(feature or "").split(".") if part]
    if len(parts) < 2:
        return str(feature or "")

    command = parts[1]
    if command in {
        "show_ip_eigrp_neighbor",
        "show_ip_eigrp_topology",
        "show_ip_eigrp_interfaces",
        "show_ip_ospf_neighbor",
        "show_ip_ospf_database",
        "show_ip_ospf_interface",
        "show_ip_rip_database",
        "show_ip_route_static",
    }:
        if len(parts) >= 3 and parts[2] in {"eigrp", "ospf", "rip", "static"}:
            return f"{command}.{parts[2]}"
        return command
    if (
        command
        in {"show_ip_interface_brief", "show_interfaces_trunk", "show_port_security"}
        and len(parts) >= 4
    ):
        return f"{command}.{parts[3]}"
    if (
        command in {"show_access_lists", "show_etherchannel_summary"}
        and len(parts) >= 4
    ):
        return f"{command}.{parts[3]}"
    if command == "show_vlan_brief":
        return "show_vlan_brief.vlan_scheme"
    if command == "show_ip_route":
        if len(parts) >= 3 and parts[2] in {
            "eigrp",
            "ospf",
            "rip",
            "static",
            "gateway",
        }:
            return f"show_ip_route.{parts[2]}"
        return "show_ip_route.routing_table"
    return command


def _verification_is_deduplicated(
    feature: str, failed_config_refs, failed_config_features
):
    layer1_ref = _verification_layer1_ref(feature)
    if layer1_ref and layer1_ref.startswith("show_running_config.routing."):
        if any(
            failed_ref == "show_running_config.routing.protocol"
            for failed_ref in failed_config_refs
        ) or any(
            str(failed_feature).startswith("show_running_config.routing.protocol")
            for failed_feature in failed_config_features
        ):
            return True, layer1_ref
    if layer1_ref and layer1_ref != "show_running_config.__vlan_scheme__":
        for failed_ref in failed_config_refs:
            if (
                failed_ref == layer1_ref
                or failed_ref.startswith(layer1_ref)
                or layer1_ref.startswith(failed_ref)
            ):
                return True, layer1_ref

    if str(feature or "").startswith("verification.show_vlan_brief."):
        for failed_feature in failed_config_features:
            if any(
                token in failed_feature
                for token in [
                    ".access_vlan",
                    ".switchport_mode",
                    ".trunk_native_vlan",
                    ".trunk_allowed_vlans",
                    ".Vlan.interface",
                    ".subinterface",
                ]
            ):
                return True, "show_running_config.__vlan_scheme__"

    if str(feature or "").startswith("verification.show_ip_route."):
        for failed_feature in failed_config_features:
            if failed_feature.startswith(
                "show_running_config.routing"
            ) or failed_feature.startswith(
                "show_running_config.switching.default_gateway"
            ):
                return True, "show_running_config.routing"

    return False, layer1_ref


def _classify_single_item(
    item, rubric_compiled, rubric_by_code, disabled_compiled, disabled_by_code
):
    status = item.get("status")
    severity = None
    rule_id = None
    rule_code = None
    matched_rule = None
    skipped_rule = None
    if status in {"missing", "extra", "mismatch"}:
        item_code = item.get("outcome_code")
        if item_code and item_code in rubric_by_code:
            matched_rule = rubric_by_code[item_code]
        elif item_code and item_code in disabled_by_code:
            skipped_rule = disabled_by_code[item_code]
        else:
            for rule, patterns, statuses in rubric_compiled:
                if statuses and status not in statuses:
                    continue
                if any(regex.search(item.get("feature", "")) for regex in patterns):
                    matched_rule = rule
                    break
            if not matched_rule:
                for rule, patterns, statuses in disabled_compiled:
                    if statuses and status not in statuses:
                        continue
                    if any(regex.search(item.get("feature", "")) for regex in patterns):
                        skipped_rule = rule
                        break
        if matched_rule:
            severity = (matched_rule.get("severity") or "minor").lower()
            rule_id = matched_rule.get("id")
            rule_code = matched_rule.get("code")
        elif skipped_rule:
            rule_id = skipped_rule.get("id")
            rule_code = skipped_rule.get("code")
        else:
            severity = "minor"

    item_copy = dict(item)
    if skipped_rule:
        item_copy["status"] = "skipped"
    if severity:
        item_copy["severity"] = severity
    if rule_id:
        item_copy["rule_id"] = rule_id
    if rule_code:
        item_copy["rule_code"] = rule_code
    return item_copy


def _classify_items(items, policy, rubric_rules=None):
    rubric_compiled, rubric_by_code, disabled_compiled, disabled_by_code = (
        _compile_rubric_rules(rubric_rules)
    )

    summary = _empty_phase1_summary()

    minor_rule_hits = set()

    config_items = [
        item for item in items if not _is_verification_feature(item.get("feature"))
    ]
    verification_items = [
        item for item in items if _is_verification_feature(item.get("feature"))
    ]

    classified_config = []
    failed_config_refs = set()
    failed_config_features = set()

    for item in config_items:
        item_copy = _classify_single_item(
            item, rubric_compiled, rubric_by_code, disabled_compiled, disabled_by_code
        )
        item_copy["layer"] = "config"
        item_copy["block_name"] = _config_block_ref(item.get("feature")) or item.get(
            "feature"
        )
        item_copy["layer1_ref"] = item_copy["block_name"]
        item_copy["counts_toward_marking"] = item_copy.get("status") in {
            "missing",
            "extra",
            "mismatch",
        }

        status = item_copy.get("status")
        if status in summary:
            summary[status] += 1
        config_key = f"config_{status}"
        if config_key in summary:
            summary[config_key] += 1

        if status in {"missing", "extra", "mismatch"}:
            failed_ref = item_copy.get("layer1_ref")
            if failed_ref:
                failed_config_refs.add(failed_ref)
            failed_config_features.add(item_copy.get("feature", ""))
            severity = item_copy.get("severity", "minor")
            rule_id = item_copy.get("rule_id")
            if severity == "major":
                summary["major"] += 1
                item_copy["rule_deduplicated"] = False
            else:
                if rule_id:
                    if rule_id not in minor_rule_hits:
                        summary["minor"] += 1
                        minor_rule_hits.add(rule_id)
                        item_copy["rule_deduplicated"] = False
                    else:
                        # Same rule already counted — still show but mark as not scored
                        item_copy["rule_deduplicated"] = True
                        item_copy["counts_toward_marking"] = False
                else:
                    summary["minor"] += 1
                    item_copy["rule_deduplicated"] = False

        classified_config.append(item_copy)

    classified_verification = []
    for item in verification_items:
        item_copy = _classify_single_item(
            item, rubric_compiled, rubric_by_code, disabled_compiled, disabled_by_code
        )
        item_copy["layer"] = "verification"
        item_copy["block_name"] = item_copy.get(
            "block_name"
        ) or _verification_block_name(item.get("feature"))
        deduplicated, layer1_ref = _verification_is_deduplicated(
            item.get("feature"), failed_config_refs, failed_config_features
        )
        item_copy["layer1_ref"] = item_copy.get("layer1_ref") or layer1_ref
        item_copy["deduplicated"] = deduplicated
        item_copy["chain_stopped"] = bool(item_copy.get("chain_stopped", False))
        item_copy["counts_toward_marking"] = (
            item_copy.get("status") in {"missing", "extra", "mismatch"}
            and not deduplicated
        )
        classified_verification.append(item_copy)

        status = item_copy.get("status")
        if status in summary:
            summary[status] += 1

        if status == "correct":
            summary["verify_correct"] += 1
        elif status in {"missing", "extra", "mismatch"}:
            if deduplicated:
                summary["verify_deduplicated"] += 1
            else:
                summary["verify_failed"] += 1
                severity = item_copy.get("severity", "minor")
                rule_id = item_copy.get("rule_id")
                if severity == "major":
                    summary["major"] += 1
                else:
                    if rule_id:
                        if rule_id not in minor_rule_hits:
                            summary["minor"] += 1
                            minor_rule_hits.add(rule_id)
                    else:
                        summary["minor"] += 1
        else:
            summary["verify_skipped"] += 1

    classified = classified_config + classified_verification
    return classified, summary, classified_config, classified_verification


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
                    "summary": _empty_phase1_summary(),
                    "hostnames": {},
                    "items": [],
                    "config_results": [],
                    "verification_results": [],
                }
            )
            continue

        items, summary, config_results, verification_results = _classify_items(
            report["items"], policy, rubric_rules
        )
        passed = _evaluate_pass_fail(summary, policy)
        report["items"] = items
        report["config_results"] = config_results
        report["verification_results"] = verification_results
        report["summary"] = summary
        report["pass"] = passed
        report["status"] = "graded"
        reports.append(report)

    return reports


_MISSING = object()


def _load_json_file(path: Path):
    try:
        with open(path, "r") as handle:
            return json.load(handle) or {}
    except Exception:
        return {}


def _resolve_json_parts(data, parts):
    """Walk into a nested dict using parts as keys.
    Handles keys that contain dots (e.g., 'GigabitEthernet0/0/1.20') by trying
    greedy multi-part matching before single-part matching.
    """

    def _walk(current, idx):
        if idx >= len(parts):
            return current
        if not isinstance(current, dict):
            return _MISSING
        # Try greedy: join parts[idx..j] and see if it's a key (longest first)
        for j in range(len(parts) - 1, idx, -1):
            candidate = ".".join(parts[idx : j + 1])
            if candidate in current:
                result = _walk(current[candidate], j + 1)
                if result is not _MISSING:
                    return result
        # Try single part
        key = parts[idx]
        if key in current:
            return _walk(current[key], idx + 1)
        return _MISSING

    return _walk(data, 0)


def _preferred_context_parts(feature: str):
    parts = [part for part in str(feature or "").split(".") if part]
    if not parts:
        return [], None

    if (
        len(parts) == 2
        and parts[0] == "show_running_config"
        and parts[1] in {"hostname", "banner_motd"}
    ):
        return parts, parts[1]

    if (
        len(parts) == 3
        and parts[0] == "show_running_config"
        and parts[1] == "switching"
        and parts[2] == "default_gateway"
    ):
        return parts, parts[2]

    if (
        len(parts) >= 4
        and parts[0] == "show_running_config"
        and parts[1] == "interfaces"
    ):
        # For subinterfaces like GigabitEthernet0/0/1.20.vlan,
        # parts = ["show_running_config", "interfaces", "GigabitEthernet0/0/1", "20", "vlan"]
        # The actual JSON key is "GigabitEthernet0/0/1.20", so we need to include
        # enough parts for _resolve_json_parts to reconstruct the dotted key.
        # Include up to the parent interface + subinterface number, leaving only
        # the terminal field name (e.g. "vlan") as the highlight_key.
        #
        # Heuristic: if parts[3] is numeric (subinterface id), include it in context.
        if len(parts) >= 5 and parts[3].isdigit():
            # e.g., parts[:4] = ["show_running_config", "interfaces", "GigabitEthernet0/0/1", "20"]
            return parts[:4], parts[4] if len(parts) > 4 else None
        # Check for "subinterface" as the field key (entire subinterface object mismatch)
        if parts[3] == "subinterface":
            return parts[:4], None
        return parts[:3], parts[3] if len(parts) > 3 else None

    if (
        len(parts) >= 5
        and parts[0] == "verification"
        and parts[1] == "show_ip_interface_brief"
        and parts[2] == "interfaces"
    ):
        return parts[:4], parts[4] if len(parts) > 4 else None

    if (
        len(parts) >= 5
        and parts[0] == "verification"
        and parts[1] == "show_vlan_brief"
        and parts[2] == "vlans"
    ):
        return parts[:4], parts[4] if len(parts) > 4 else None

    if len(parts) >= 2:
        return parts[:-1], parts[-1]

    return parts, None


def _extract_error_context(
    template_config, student_config, feature: str, expected=None, actual=None
):
    parts, highlight_key = _preferred_context_parts(feature)
    if not parts:
        return {
            "context_path": "",
            "highlight_key": None,
            "template_context": None,
            "student_context": None,
        }

    # Special handling for subinterface mismatches:
    # For features like .GigabitEthernet0/0/1.subinterface, show only the paired
    # expected/actual subinterfaces from the comparison result rather than every
    # sibling subinterface under the same parent.
    feature_parts = [p for p in str(feature or "").split(".") if p]
    if (
        len(feature_parts) >= 4
        and feature_parts[0] == "show_running_config"
        and feature_parts[1] == "interfaces"
        and feature_parts[3] == "subinterface"
    ):
        parent_iface = feature_parts[2]
        t_ifaces = _resolve_json_parts(
            template_config, ["show_running_config", "interfaces"]
        )
        s_ifaces = _resolve_json_parts(
            student_config, ["show_running_config", "interfaces"]
        )
        if t_ifaces is not _MISSING or s_ifaces is not _MISSING:
            expected_name = expected.get("name") if isinstance(expected, dict) else None
            actual_name = actual.get("name") if isinstance(actual, dict) else None
            t_subs = None
            s_subs = None
            if isinstance(t_ifaces, dict):
                if expected_name and expected_name in t_ifaces:
                    t_subs = {expected_name: t_ifaces.get(expected_name)}
                else:
                    t_subs = {
                        k: v
                        for k, v in t_ifaces.items()
                        if k.startswith(f"{parent_iface}.")
                    }
            if isinstance(s_ifaces, dict):
                if actual_name and actual_name in s_ifaces:
                    s_subs = {actual_name: s_ifaces.get(actual_name)}
                else:
                    s_subs = {
                        k: v
                        for k, v in s_ifaces.items()
                        if k.startswith(f"{parent_iface}.")
                    }
            return {
                "context_path": f"show_running_config.interfaces.{parent_iface}.*",
                "highlight_key": "subinterface",
                "template_context": t_subs if t_subs else None,
                "student_context": s_subs if s_subs else None,
            }

    if (
        len(feature_parts) >= 3
        and feature_parts[0] == "show_running_config"
        and feature_parts[1] == "users"
    ):
        username = feature_parts[2]
        t_users = _resolve_json_parts(template_config, ["show_running_config", "users"])
        s_users = _resolve_json_parts(student_config, ["show_running_config", "users"])

        def _find_user(users_list, wanted):
            if not isinstance(users_list, list):
                return _MISSING
            for entry in users_list:
                if (
                    isinstance(entry, dict)
                    and str(entry.get("username") or "").lower() == wanted.lower()
                ):
                    return entry
            return _MISSING

        template_user = _find_user(t_users, username)
        student_user = _find_user(s_users, username)
        if template_user is not _MISSING or student_user is not _MISSING:
            return {
                "context_path": f"show_running_config.users.{username}",
                "highlight_key": username,
                "template_context": (
                    None if template_user is _MISSING else template_user
                ),
                "student_context": None if student_user is _MISSING else student_user,
            }

    if (
        len(feature_parts) >= 3
        and feature_parts[0] == "show_running_config"
        and feature_parts[1] == "access_lists"
    ):
        acl_name = feature_parts[2]
        t_acls = _resolve_json_parts(
            template_config, ["show_running_config", "access_lists"]
        )
        s_acls = _resolve_json_parts(
            student_config, ["show_running_config", "access_lists"]
        )

        template_acl = (
            t_acls.get(acl_name, _MISSING) if isinstance(t_acls, dict) else _MISSING
        )
        student_acl = (
            s_acls.get(acl_name, _MISSING) if isinstance(s_acls, dict) else _MISSING
        )

        if template_acl is not _MISSING or student_acl is not _MISSING:
            return {
                "context_path": f"show_running_config.access_lists.{acl_name}",
                "highlight_key": acl_name,
                "template_context": None if template_acl is _MISSING else template_acl,
                "student_context": None if student_acl is _MISSING else student_acl,
            }

    if (
        len(feature_parts) >= 3
        and feature_parts[0] == "show_running_config"
        and feature_parts[1] == "nat"
    ):
        nat_field = feature_parts[2]
        t_nat = _resolve_json_parts(template_config, ["show_running_config", "nat"])
        s_nat = _resolve_json_parts(student_config, ["show_running_config", "nat"])

        template_nat = (
            t_nat.get(nat_field, _MISSING) if isinstance(t_nat, dict) else _MISSING
        )
        student_nat = (
            s_nat.get(nat_field, _MISSING) if isinstance(s_nat, dict) else _MISSING
        )

        if template_nat is not _MISSING or student_nat is not _MISSING:
            return {
                "context_path": f"show_running_config.nat.{nat_field}",
                "highlight_key": nat_field,
                "template_context": None if template_nat is _MISSING else template_nat,
                "student_context": None if student_nat is _MISSING else student_nat,
            }

    if (
        len(feature_parts) >= 3
        and feature_parts[0] == "show_running_config"
        and feature_parts[1] == "routing"
        and feature_parts[2] == "protocol"
    ):
        t_routing = _resolve_json_parts(
            template_config, ["show_running_config", "routing"]
        )
        s_routing = _resolve_json_parts(
            student_config, ["show_running_config", "routing"]
        )

        expected_protocols = expected if isinstance(expected, list) else []
        actual_protocols = actual if isinstance(actual, list) else []

        def _protocol_subset(routing_obj, protocols):
            if not isinstance(routing_obj, dict):
                return _MISSING
            subset = {}
            for proto in protocols:
                value = routing_obj.get(proto, _MISSING)
                if value is not _MISSING:
                    subset[proto] = value
            return subset if subset else _MISSING

        template_subset = _protocol_subset(t_routing, expected_protocols)
        student_subset = _protocol_subset(s_routing, actual_protocols)

        if template_subset is not _MISSING or student_subset is not _MISSING:
            return {
                "context_path": "show_running_config.routing.protocol",
                "highlight_key": "protocol",
                "template_context": (
                    None if template_subset is _MISSING else template_subset
                ),
                "student_context": (
                    None if student_subset is _MISSING else student_subset
                ),
            }

    while parts:
        template_context = _resolve_json_parts(template_config, parts)
        student_context = _resolve_json_parts(student_config, parts)
        if template_context is not _MISSING or student_context is not _MISSING:
            return {
                "context_path": ".".join(parts),
                "highlight_key": highlight_key,
                "template_context": (
                    None if template_context is _MISSING else template_context
                ),
                "student_context": (
                    None if student_context is _MISSING else student_context
                ),
            }
        parts = parts[:-1]

    return {
        "context_path": "",
        "highlight_key": None,
        "template_context": None,
        "student_context": None,
    }


def _normalize_text(value):
    lowered = str(value or "").lower()
    for char in ["_", "-", ".", "(", ")", "[", "]"]:
        lowered = lowered.replace(char, " ")
    return " ".join(lowered.split())


def _command_hint_for_feature(feature: str):
    if feature.startswith("show_running_config."):
        return "show running-config"
    mapping = {
        "verification.show_ip_interface_brief.": "show ip interface brief",
        "verification.show_ip_route.": "show ip route",
        "verification.show_ip_eigrp_neighbor.": "show ip eigrp neighbor",
        "verification.show_ip_eigrp_topology.": "show ip eigrp topology",
        "verification.show_ip_eigrp_interfaces.": "show ip eigrp interfaces",
        "verification.show_ip_ospf_neighbor.": "show ip ospf neighbor",
        "verification.show_ip_ospf_database.": "show ip ospf database",
        "verification.show_ip_ospf_interface.": "show ip ospf interface",
        "verification.show_ip_rip_database.": "show ip rip database",
        "verification.show_ip_route_static.": "show ip route static",
        "verification.show_vlan_brief.": "show vlan brief",
        "verification.show_access_lists.": "show access-lists",
        "verification.show_interfaces_trunk.": "show interfaces trunk",
        "verification.show_port_security.": "show port-security",
        "verification.show_etherchannel_summary.": "show etherchannel summary",
        "verification.show_ip_nat_statistics.": "show ip nat statistics",
        "verification.show_ip_nat_translations.": "show ip nat translations",
        "verification.show_ip_dhcp_binding.": "show ip dhcp binding",
        "verification.show_ip_dhcp_pool.": "show ip dhcp pool",
    }
    for prefix, hint in mapping.items():
        if feature.startswith(prefix):
            return hint
    return None


def _command_aliases(command_hint: str):
    normalized = _normalize_text(command_hint)
    aliases = {normalized}

    explicit_aliases = {
        "show running config": {
            "show running config",
            "show run",
            "sh run",
            "show running-config",
            "show running_config",
            "show_run",
            "sh_run",
        },
        "show ip interface brief": {
            "show ip interface brief",
            "show ip int brief",
            "sh ip interface brief",
            "sh ip int brief",
            "show interface brief",
            "show int brief",
            "sh int brief",
        },
        "show ip route": {
            "show ip route",
            "sh ip route",
        },
        "show vlan brief": {
            "show vlan brief",
            "sh vlan brief",
        },
        "show access lists": {
            "show access lists",
            "show access-lists",
            "show access_lists",
            "sh access lists",
            "sh access-lists",
            "sh access_lists",
        },
        "show interfaces trunk": {
            "show interfaces trunk",
            "show interface trunk",
            "show int trunk",
            "sh interfaces trunk",
            "sh interface trunk",
            "sh int trunk",
        },
        "show port security": {
            "show port security",
            "show port-security",
            "show port_security",
            "sh port security",
            "sh port-security",
            "sh port_security",
        },
        "show etherchannel summary": {
            "show etherchannel summary",
            "sh etherchannel summary",
        },
        "show ip nat statistics": {
            "show ip nat statistics",
            "sh ip nat statistics",
            "show ip nat stats",
            "sh ip nat stats",
        },
        "show ip nat translations": {
            "show ip nat translations",
            "sh ip nat translations",
        },
        "show ip dhcp binding": {
            "show ip dhcp binding",
            "sh ip dhcp binding",
        },
        "show ip dhcp pool": {
            "show ip dhcp pool",
            "sh ip dhcp pool",
        },
    }

    for alias in explicit_aliases.get(normalized, set()):
        aliases.add(_normalize_text(alias))

    return {alias for alias in aliases if alias}


def _find_log_file(log_dir: Path, command_hint: str):
    if not log_dir or not log_dir.is_dir() or not command_hint:
        return None
    desired_aliases = _command_aliases(command_hint)
    candidates = []
    for entry in sorted(log_dir.iterdir()):
        if not entry.is_file():
            continue
        if entry.name.lower() in {"config.json", "logs.json", "summary.json"}:
            continue
        score = 0
        normalized = _normalize_text(entry.name)
        if normalized in desired_aliases:
            score = 0
        elif any(
            alias in normalized or normalized in alias for alias in desired_aliases
        ):
            score = 1
        else:
            continue
        candidates.append((score, len(entry.name), entry))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1], item[2].name))
    return candidates[0][2]


def _read_text_lines(path: Path):
    if not path or not path.exists():
        return []
    try:
        return path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return []


def _find_line_index(lines, matcher):
    for index, line in enumerate(lines):
        try:
            if matcher(line):
                return index
        except Exception:
            continue
    return None


def _extract_cli_block(lines, start_index):
    if start_index is None or start_index < 0 or start_index >= len(lines):
        return None
    block = [lines[start_index]]
    for idx in range(start_index + 1, len(lines)):
        line = lines[idx]
        stripped = line.strip()
        if not stripped:
            block.append(line)
            continue
        if stripped == "!":
            block.append(line)
            break
        if not line.startswith(" "):
            break
        block.append(line)
    return "\n".join(block).strip()


def _extract_matching_lines(lines, matcher, max_matches=12):
    matched = []
    for line in lines:
        try:
            if matcher(line):
                matched.append(line)
                if len(matched) >= max_matches:
                    break
        except Exception:
            continue
    return "\n".join(matched).strip() if matched else None


def _excerpt_around(lines, index, before=2, after=2):
    if index is None or index < 0 or index >= len(lines):
        return None
    start = max(0, index - before)
    end = min(len(lines), index + after + 1)
    return "\n".join(lines[start:end]).strip()


def _interface_aliases(interface_name: str):
    text = str(interface_name or "").strip()
    if not text:
        return []
    aliases = {text}
    replacements = {
        "GigabitEthernet": "Gi",
        "FastEthernet": "Fa",
        "Serial": "Se",
        "Loopback": "Lo",
        "Vlan": "Vl",
        "Port-channel": "Po",
        "Tunnel": "Tu",
    }
    for long_name, short_name in replacements.items():
        if text.startswith(long_name):
            aliases.add(short_name + text[len(long_name) :])
    return sorted(aliases, key=len, reverse=True)


def _extract_running_config_excerpt(lines, feature: str, expected=None, actual=None):
    parts = [part for part in str(feature or "").split(".") if part]
    if not parts:
        return None

    def _extract_router_protocol_block(proto_name):
        proto = str(proto_name or "").lower()
        if proto == "eigrp":
            idx = _find_line_index(
                lines, lambda line: line.strip().lower().startswith("router eigrp ")
            )
            return _extract_cli_block(lines, idx)
        if proto == "ospf":
            idx = _find_line_index(
                lines, lambda line: line.strip().lower().startswith("router ospf ")
            )
            return _extract_cli_block(lines, idx)
        if proto == "rip":
            idx = _find_line_index(
                lines, lambda line: line.strip().lower() == "router rip"
            )
            return _extract_cli_block(lines, idx)
        return None

    if len(parts) >= 4 and parts[1] == "interfaces":
        iface = parts[2]
        # For subinterface features, try finding the subinterface block first
        # e.g., feature = show_running_config.interfaces.GigabitEthernet0/0/1.20.vlan
        # parts = ["show_running_config", "interfaces", "GigabitEthernet0/0/1", "20", "vlan"]
        if len(parts) >= 5 and parts[3].isdigit():
            sub_iface = f"{iface}.{parts[3]}"  # e.g., GigabitEthernet0/0/1.20
            idx = _find_line_index(
                lines,
                lambda line, si=sub_iface: line.strip().lower()
                == f"interface {si}".lower(),
            )
            block = _extract_cli_block(lines, idx)
            if block:
                return block
        # For "subinterface" field, try to find any subinterface of this parent
        if len(parts) >= 4 and parts[3] == "subinterface":
            expected_name = expected.get("name") if isinstance(expected, dict) else None
            actual_name = actual.get("name") if isinstance(actual, dict) else None
            pair_names = [name for name in [expected_name, actual_name] if name]
            sub_blocks = []
            if pair_names:
                for sub_name in pair_names:
                    idx = _find_line_index(
                        lines,
                        lambda line, si=sub_name: line.strip().lower()
                        == f"interface {si}".lower(),
                    )
                    block = _extract_cli_block(lines, idx)
                    if block:
                        sub_blocks.append(block)
            else:
                # Fallback: show all subinterface blocks for this parent
                for i, line in enumerate(lines):
                    stripped = line.strip().lower()
                    if stripped.startswith(f"interface {iface.lower()}."):
                        block = _extract_cli_block(lines, i)
                        if block:
                            sub_blocks.append(block)
            if sub_blocks:
                return "\n\n".join(sub_blocks)
        # Fallback: parent interface
        idx = _find_line_index(
            lines, lambda line: line.strip().lower() == f"interface {iface}".lower()
        )
        block = _extract_cli_block(lines, idx)
        if block:
            return block

    if len(parts) >= 3 and parts[1] == "vty":
        idx = _find_line_index(
            lines, lambda line: line.strip().lower().startswith("line vty ")
        )
        block = _extract_cli_block(lines, idx)
        if block:
            return block

    if len(parts) >= 3 and parts[1] == "routing":
        routing_key = parts[2].lower()
        if routing_key == "protocol":
            protocols = (
                expected
                if isinstance(expected, list) and expected
                else actual if isinstance(actual, list) else []
            )
            blocks = []
            for proto in protocols:
                block = _extract_router_protocol_block(proto)
                if block:
                    blocks.append(block)
            if blocks:
                return "\n\n".join(blocks)
            return "No matching routing protocol block found in running-config."
        if routing_key == "ospf":
            idx = _find_line_index(
                lines, lambda line: line.strip().lower().startswith("router ospf ")
            )
            block = _extract_cli_block(lines, idx)
            if block:
                return block
        if routing_key == "eigrp":
            idx = _find_line_index(
                lines, lambda line: line.strip().lower().startswith("router eigrp ")
            )
            block = _extract_cli_block(lines, idx)
            if block:
                return block
        if routing_key == "static_routes":
            block = _extract_matching_lines(
                lines, lambda line: line.strip().lower().startswith("ip route ")
            )
            if block:
                return block

    if len(parts) >= 3 and parts[1] == "dhcp_pools":
        pool_name = parts[2]
        idx = _find_line_index(
            lines,
            lambda line: line.strip().lower() == f"ip dhcp pool {pool_name}".lower(),
        )
        block = _extract_cli_block(lines, idx)
        if block:
            return block

    if len(parts) >= 2 and parts[1] == "dhcp_excluded":
        block = _extract_matching_lines(
            lines,
            lambda line: line.strip().lower().startswith("ip dhcp excluded-address"),
        )
        if block:
            return block

    if len(parts) >= 3 and parts[1] == "access_lists":
        acl_name = parts[2]
        idx = _find_line_index(
            lines,
            lambda line: line.strip().lower().startswith(f"ip access-list ")
            and line.strip().split()[-1].lower() == acl_name.lower(),
        )
        block = _extract_cli_block(lines, idx)
        if block:
            return block
        block = _extract_matching_lines(
            lines,
            lambda line: line.strip()
            .lower()
            .startswith(f"access-list {acl_name.lower()} "),
        )
        if block:
            return block
        return f"No ACL named '{acl_name}' found in running-config."

    prefix_map = {
        "show_running_config.hostname": "hostname ",
        "show_running_config.banner_motd": "banner motd",
        "show_running_config.switching.default_gateway": "ip default-gateway ",
    }
    for prefix, needle in prefix_map.items():
        if feature.startswith(prefix):
            idx = _find_line_index(
                lines, lambda line: line.strip().lower().startswith(needle)
            )
            excerpt = _excerpt_around(lines, idx, before=0, after=0)
            if excerpt:
                return excerpt

    if feature.startswith("show_running_config.switching.spanning_tree"):
        block = _extract_matching_lines(
            lines, lambda line: line.strip().lower().startswith("spanning-tree ")
        )
        if block:
            return block

    if feature.startswith("show_running_config.http_server"):
        block = _extract_matching_lines(
            lines, lambda line: line.strip().lower().startswith("ip http ")
        )
        if block:
            return block

    if feature.startswith("show_running_config.users."):
        parts = feature.split(".")
        username = parts[2] if len(parts) > 2 else None
        block = _extract_matching_lines(
            lines,
            lambda line: line.strip().lower().startswith("username ")
            and (not username or line.strip().split()[1].lower() == username.lower()),
        )
        if block:
            return block
        if username:
            return f"No 'username {username}' command found in running-config."

    if feature.startswith("show_running_config.nat."):
        nat_field = parts[2] if len(parts) > 2 else ""
        if nat_field == "inside_interfaces":
            block = _extract_matching_lines(
                lines, lambda line: line.strip().lower() == "ip nat inside"
            )
            if block:
                return block
            return "No 'ip nat inside' configuration found in running-config."
        if nat_field == "outside_interfaces":
            block = _extract_matching_lines(
                lines, lambda line: line.strip().lower() == "ip nat outside"
            )
            if block:
                return block
            return "No 'ip nat outside' configuration found in running-config."
        if nat_field == "pools":
            block = _extract_matching_lines(
                lines, lambda line: line.strip().lower().startswith("ip nat pool ")
            )
            if block:
                return block
            return "No 'ip nat pool' configuration found in running-config."
        if nat_field == "inside_source":
            block = _extract_matching_lines(
                lines,
                lambda line: line.strip().lower().startswith("ip nat inside source "),
            )
            if block:
                return block
            return "No 'ip nat inside source' configuration found in running-config."
        block = _extract_matching_lines(
            lines, lambda line: "ip nat" in line.strip().lower()
        )
        if block:
            return block

    return None


def _extract_show_ip_interface_brief_excerpt(lines, feature: str):
    parts = [part for part in str(feature or "").split(".") if part]
    if len(parts) < 5 or parts[2] != "interfaces":
        return None
    iface = parts[3]
    aliases = _interface_aliases(iface)
    idx = _find_line_index(
        lines,
        lambda line: any(
            re.search(rf"^{re.escape(alias)}(\s|$)", line.strip(), re.IGNORECASE)
            for alias in aliases
        ),
    )
    return _excerpt_around(lines, idx, before=2, after=2)


def _extract_show_vlan_brief_excerpt(lines, feature: str):
    parts = [part for part in str(feature or "").split(".") if part]
    if len(parts) < 4 or parts[2] != "vlans":
        return None
    vlan_id = parts[3]
    idx = _find_line_index(
        lines, lambda line: re.search(rf"^{re.escape(vlan_id)}\s", line.strip())
    )
    return _excerpt_around(lines, idx, before=2, after=1)


def _extract_show_ip_route_excerpt(lines, feature: str, expected=None, actual=None):
    candidates = []
    for value in [expected, actual]:
        if isinstance(value, str) and value.strip():
            candidates.append(value.strip())
        elif isinstance(value, dict):
            for key in ["destination", "network", "next_hop", "interface"]:
                val = value.get(key)
                if isinstance(val, str) and val.strip() and val.strip() != "-":
                    candidates.append(val.strip())
    for candidate in candidates:
        idx = _find_line_index(lines, lambda line: candidate.lower() in line.lower())
        excerpt = _excerpt_around(lines, idx, before=2, after=2)
        if excerpt:
            return excerpt
    return "\n".join(lines[:8]).strip() if lines else None


def _extract_show_access_lists_excerpt(lines, feature: str):
    parts = [part for part in str(feature or "").split(".") if part]
    acl_name = (
        parts[3] if len(parts) >= 4 and parts[2] in {"acls", "access_lists"} else None
    )
    if acl_name:
        idx = _find_line_index(
            lines,
            lambda line: re.search(rf"\b{re.escape(acl_name)}\b", line, re.IGNORECASE),
        )
        if idx is not None:
            start = idx
            while start > 0 and lines[start - 1].strip():
                start -= 1
            end = idx
            while end + 1 < len(lines) and lines[end + 1].strip():
                end += 1
            return "\n".join(lines[start : end + 1]).strip()
    return "\n".join(lines[:12]).strip() if lines else None


def _extract_raw_excerpt(log_path: Path, feature: str, expected=None, actual=None):
    lines = _read_text_lines(log_path)
    if not lines:
        return None

    if feature.startswith("show_running_config."):
        excerpt = _extract_running_config_excerpt(
            lines, feature, expected=expected, actual=actual
        )
        if excerpt:
            return excerpt
        if feature == "show_running_config.banner_motd":
            return "No 'banner motd' command found in running-config."
        if feature == "show_running_config.hostname":
            return "No 'hostname' command found in running-config."

    if feature.startswith("verification.show_ip_interface_brief."):
        excerpt = _extract_show_ip_interface_brief_excerpt(lines, feature)
        if excerpt:
            return excerpt

    if feature.startswith("verification.show_vlan_brief."):
        excerpt = _extract_show_vlan_brief_excerpt(lines, feature)
        if excerpt:
            return excerpt

    if feature.startswith("verification.show_ip_route."):
        excerpt = _extract_show_ip_route_excerpt(
            lines, feature, expected=expected, actual=actual
        )
        if excerpt:
            return excerpt

    if feature.startswith("verification.show_access_lists."):
        excerpt = _extract_show_access_lists_excerpt(lines, feature)
        if excerpt:
            return excerpt

    search_terms = []
    for part in str(feature or "").split("."):
        if "/" in part or part.lower().startswith(
            ("vlan", "fa", "gi", "se", "lo", "po")
        ):
            search_terms.append(part)
    for value in [expected, actual]:
        if isinstance(value, str) and value.strip() and len(value.strip()) < 80:
            search_terms.append(value.strip())
    for term in search_terms:
        idx = _find_line_index(lines, lambda line: str(term).lower() in line.lower())
        excerpt = _excerpt_around(lines, idx, before=2, after=2)
        if excerpt:
            return excerpt

    return "\n".join(lines[:12]).strip()


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


def _hostname_matches_target(expected, actual):
    expected_name = str(expected or "").strip().upper()
    actual_name = str(actual or "").strip().upper()
    if not expected_name or not actual_name:
        return True
    return expected_name == actual_name


def _engine_student_logs_dir(
    classroom, tutor_name, time_slot, student_id, hostname=None
):
    safe_classroom = str(classroom or "").strip()
    safe_tutor = str(tutor_name or "").strip()
    safe_time = str(time_slot or "").strip()
    safe_student = str(student_id or "").strip()
    if not all([safe_classroom, safe_tutor, safe_time, safe_student]):
        return None
    if safe_student.lower() in {"sample", "unknown"}:
        return None
    target_dir = (
        ENGINE_STUDENTS_DIR / safe_classroom / safe_tutor / safe_time / safe_student
    )
    if hostname:
        target_dir = target_dir / str(hostname).strip()
    return target_dir


def _delete_engine_student_logs_for_docs_target(target):
    try:
        relative = target.resolve().relative_to(DOCS_DIR)
    except Exception:
        return

    if len(relative.parts) < 1:
        return

    mirror_target = ENGINE_STUDENTS_DIR.joinpath(*relative.parts)
    if mirror_target.exists():
        shutil.rmtree(mirror_target)


def _session_student_names_path(session_dir: Path) -> Path:
    return session_dir / "students.json"


def _load_session_student_names(session_dir: Path) -> dict:
    path = _session_student_names_path(session_dir)
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle) or {}
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items() if str(k).strip()}
    except Exception:
        return {}
    return {}


def _save_session_student_names(session_dir: Path, names: dict):
    path = _session_student_names_path(session_dir)
    cleaned = {
        str(k): str(v)
        for k, v in (names or {}).items()
        if str(k).strip() and str(v).strip()
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(cleaned, handle, indent=2, ensure_ascii=False)


def _save_output_to_engine_students(
    command, output, classroom, tutor_name, time_slot, student_id, hostname
):
    """
    Save command output under
    comparison_engine/students/<classroom>/<tutor_name>/<time_slot>/<student_id>/<hostname>/.
    Only stores command logs (no config.json).
    """
    if not hostname:
        return None
    target_dir = _engine_student_logs_dir(
        classroom, tutor_name, time_slot, student_id, hostname
    )
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
    classroom = (
        data.get("classroom") or data.get("examName") or data.get("exam_name") or ""
    ).strip()
    tutor_name = (
        data.get("tutor_name")
        or data.get("tutorName")
        or data.get("sessionId")
        or data.get("session_id")
        or ""
    ).strip()
    time_slot = (data.get("time_slot") or data.get("timeSlot") or "").strip()
    student_id = (data.get("studentId") or data.get("student_id") or "").strip()

    if not all([classroom, tutor_name, time_slot, student_id]):
        return (
            None,
            jsonify(
                {
                    "status": "error",
                    "message": "Missing classroom/tutor_name/time_slot/studentId",
                }
            ),
            400,
        )
    return (classroom, tutor_name, time_slot, student_id), None, None


@app.route("/api/create_directory", methods=["POST"])
def api_create_directory():
    """
    Create the standard directory hierarchy for a student.
    """
    data = request.get_json() or {}
    validated, error_resp, status = _validate_directory_payload(data)
    if error_resp:
        return error_resp, status

    classroom, tutor_name, time_slot, student_id = validated
    student_name = (data.get("studentName") or data.get("student_name") or "").strip()
    base_path = os.path.expanduser(
        os.path.join("~/Documents", classroom, tutor_name, time_slot, student_id)
    )
    os.makedirs(base_path, exist_ok=True)
    if student_name:
        session_dir = Path.home() / "Documents" / classroom / tutor_name / time_slot
        names = _load_session_student_names(session_dir)
        names[student_id] = student_name
        _save_session_student_names(session_dir, names)
    return jsonify(
        {
            "status": "ok",
            "message": f"Directory ready: {base_path}",
            "path": base_path,
            "classroom": classroom,
            "tutor_name": tutor_name,
            "time_slot": time_slot,
            # Backward-compatible response keys
            "exam_name": classroom,
            "session_id": tutor_name,
            "student_id": student_id,
            "student_name": student_name,
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
        if len(parts) >= 4:
            classroom, tutor_name, time_slot, student_id = (
                parts[-4],
                parts[-3],
                parts[-2],
                parts[-1],
            )
        else:
            classroom = (
                data.get("classroom") or data.get("examName") or data.get("exam_name")
            )
            tutor_name = (
                data.get("tutor_name")
                or data.get("tutorName")
                or data.get("sessionId")
                or data.get("session_id")
            )
            time_slot = data.get("time_slot") or data.get("timeSlot")
            student_id = data.get("studentId") or data.get("student_id")
        return jsonify(
            {
                "status": "ok",
                "message": f"Using existing directory: {existing_path}",
                "path": existing_path,
                "classroom": classroom,
                "tutor_name": tutor_name,
                "time_slot": time_slot,
                # Backward-compatible response keys
                "exam_name": classroom,
                "session_id": tutor_name,
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

    for classroom_dir in docs_path.iterdir():
        if not classroom_dir.is_dir() or classroom_dir.name.startswith("."):
            continue
        for tutor_dir in classroom_dir.iterdir():
            if not tutor_dir.is_dir() or tutor_dir.name.startswith("."):
                continue
            for time_dir in tutor_dir.iterdir():
                if not time_dir.is_dir() or time_dir.name.startswith("."):
                    continue
                student_names = _load_session_student_names(time_dir)
                for student_dir in time_dir.iterdir():
                    if not student_dir.is_dir() or student_dir.name.startswith("."):
                        continue
                    results.append(
                        {
                            "path": str(student_dir),
                            "classroom": classroom_dir.name,
                            "tutor_name": tutor_dir.name,
                            "time_slot": time_dir.name,
                            # Backward-compatible keys
                            "exam_name": classroom_dir.name,
                            "session_id": tutor_dir.name,
                            "student_id": student_dir.name,
                            "student_name": student_names.get(student_dir.name, ""),
                            "display": (
                                f"{classroom_dir.name}/{tutor_dir.name}/"
                                f"{time_dir.name}/{student_dir.name}"
                            ),
                        }
                    )
    return sorted(results, key=lambda x: x["display"])


def _list_existing_sessions():
    docs_path = Path.home() / "Documents"
    results = []
    if not docs_path.exists():
        return results

    for classroom_dir in docs_path.iterdir():
        if not classroom_dir.is_dir() or classroom_dir.name.startswith("."):
            continue
        for tutor_dir in classroom_dir.iterdir():
            if not tutor_dir.is_dir() or tutor_dir.name.startswith("."):
                continue
            for time_dir in tutor_dir.iterdir():
                if not time_dir.is_dir() or time_dir.name.startswith("."):
                    continue
                results.append(
                    {
                        "path": str(time_dir),
                        "classroom": classroom_dir.name,
                        "tutor_name": tutor_dir.name,
                        "time_slot": time_dir.name,
                        "exam_name": classroom_dir.name,
                        "session_id": tutor_dir.name,
                        "display": f"{classroom_dir.name}/{tutor_dir.name}/{time_dir.name}",
                    }
                )
    return sorted(results, key=lambda x: x["display"])


def _list_existing_exams():
    docs_path = Path.home() / "Documents"
    results = []
    if not docs_path.exists():
        return results

    for classroom_dir in docs_path.iterdir():
        if not classroom_dir.is_dir() or classroom_dir.name.startswith("."):
            continue
        # Only include dirs that contain at least one tutor/time subdirectory
        has_session = any(
            d.is_dir() for d in classroom_dir.iterdir() if not d.name.startswith(".")
        )
        if has_session:
            results.append(
                {
                    "path": str(classroom_dir),
                    "classroom": classroom_dir.name,
                    "exam_name": classroom_dir.name,
                    "display": classroom_dir.name,
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

    return jsonify(
        {"status": "ok", "directories": directories, "current_path": str(current)}
    )


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
                subfolders.append({"name": item.name, "path": str(item)})
        subfolders.sort(key=lambda x: x["name"].lower())
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

    return jsonify(
        {
            "status": "ok",
            "subfolders": subfolders,
            "current_path": str(target),
            "parent_path": str(target.parent),
        }
    )


@app.route("/api/directories/bulk", methods=["POST"])
def api_bulk_directories():
    data = request.get_json() or {}
    classroom = (
        data.get("classroom") or data.get("examName") or data.get("exam_name") or ""
    ).strip()
    tutor_name = (
        data.get("tutor_name")
        or data.get("tutorName")
        or data.get("sessionId")
        or data.get("session_id")
        or ""
    ).strip()
    time_slot = (data.get("time_slot") or data.get("timeSlot") or "").strip()
    students = data.get("students") or []

    if not classroom or not tutor_name or not time_slot or not students:
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "Missing classroom/tutor_name/time_slot/students for bulk creation.",
                }
            ),
            400,
        )

    created = []
    base_docs_path = Path.home() / "Documents"
    session_dir = base_docs_path / classroom / tutor_name / time_slot
    session_dir.mkdir(parents=True, exist_ok=True)
    student_names = _load_session_student_names(session_dir)

    for student in students:
        student_id = (student.get("id") or "").strip()
        student_name = (student.get("name") or "").strip()
        if not student_id:
            continue
        student_dir = session_dir / student_id
        student_dir.mkdir(parents=True, exist_ok=True)
        if student_name:
            student_names[student_id] = student_name
        created.append(
            {
                "path": str(student_dir),
                "classroom": classroom,
                "tutor_name": tutor_name,
                "time_slot": time_slot,
                "exam_name": classroom,
                "session_id": tutor_name,
                "student_id": student_id,
                "student_name": student_name,
                "display": f"{classroom}/{tutor_name}/{time_slot}/{student_id}",
            }
        )

    _save_session_student_names(session_dir, student_names)

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
        raw_port = (
            ssh.get("port")
            or data.get("port")
            or last_used_ssh_credentials.get("port", 22)
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

        print(f"[API][connect][ssh] Connecting to {host}:{port_value} ...", flush=True)
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


@app.route("/api/reset_device", methods=["POST"])
def api_reset_device():
    data = request.get_json() or {}
    mode = (data.get("mode") or data.get("connection") or "serial").lower()
    if mode != "serial":
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "Device reset is only supported over serial.",
                }
            ),
            400,
        )

    serial_payload = data.get("serial") or {}
    with connection_lock:
        stored_port = last_used_serial_settings.get("port")
        stored_baud = last_used_serial_settings.get("baudrate", 9600)
    port = (
        serial_payload.get("port") or data.get("port") or stored_port or "/dev/ttyUSB0"
    )
    raw_baud = (
        serial_payload.get("baudrate")
        or serial_payload.get("baud")
        or data.get("baudrate")
        or stored_baud
        or 9600
    )
    try:
        baudrate = int(raw_baud)
    except (TypeError, ValueError):
        baudrate = 9600

    if not port:
        return (
            jsonify({"status": "error", "message": "No serial port configured."}),
            400,
        )

    _close_serial_connection()
    _close_ssh_connection()

    result = reload_cisco_device(port=port, baudrate=baudrate)
    logs = result.get("logs") or []
    message = result.get("message") or "Reset completed."
    if result.get("success"):
        return jsonify(
            {
                "status": "ok",
                "message": message,
                "logs": logs,
                "port": port,
                "baudrate": baudrate,
            }
        )
    return (
        jsonify(
            {
                "status": "error",
                "message": message,
                "logs": logs,
                "port": port,
                "baudrate": baudrate,
            }
        ),
        500,
    )


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
    classroom = (
        data.get("classroom") or data.get("exam_name") or data.get("examName") or ""
    ).strip()
    tutor_name = (
        data.get("tutor_name") or data.get("session_id") or data.get("sessionId") or ""
    ).strip()
    time_slot = (data.get("time_slot") or data.get("timeSlot") or "").strip()
    student_id = data.get("student_id")
    filename = data.get("filename", "log.txt")
    content = data.get("content", "")

    if not (classroom and tutor_name and time_slot and student_id):
        return jsonify({"status": "error", "message": "Missing directory info"}), 400

    base_dir = os.path.expanduser(
        os.path.join("~/Documents", classroom, tutor_name, time_slot, student_id)
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
    classroom = data.get("classroom") or data.get("exam_name") or data.get("examName")
    tutor_name = (
        data.get("tutor_name") or data.get("session_id") or data.get("sessionId")
    )
    time_slot = data.get("time_slot") or data.get("timeSlot")
    student_id = data.get("student_id")

    if mode == "existing":
        if not log_dir:
            raise ValueError("Missing log_dir for existing directory mode.")
        expanded = _expand_path(log_dir)
        if not expanded or not os.path.exists(expanded):
            raise FileNotFoundError(f"Existing directory not found: {log_dir}")
        return expanded, classroom, tutor_name, time_slot, student_id

    if not all([classroom, tutor_name, time_slot, student_id]):
        raise ValueError(
            "Missing classroom/tutor/time/student details for directory creation."
        )

    base_path = os.path.expanduser(
        os.path.join("~/Documents", classroom, tutor_name, time_slot, student_id)
    )
    os.makedirs(base_path, exist_ok=True)
    return base_path, classroom, tutor_name, time_slot, student_id


@app.route("/api/abort", methods=["POST"])
def api_abort():
    """Signal the running execution to stop immediately."""
    execution_abort.set()
    # Close connections to force any blocking read to fail
    _close_serial_connection()
    _close_ssh_connection()
    return jsonify({"status": "ok", "message": "Abort signal sent."})


@app.route("/api/execute", methods=["POST"])
def api_execute():
    execution_abort.clear()
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
        base_path, classroom, tutor_name, time_slot, student_id = _ensure_base_path(
            data
        )
    except FileNotFoundError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400

    def generate():
        hostname = None
        files_written = []
        skip_config = bool(data.get("skip_config"))
        skip_hostname_check = bool(data.get("skip_hostname_check"))

        def run_serial():
            global current_mode
            nonlocal hostname
            serial_payload = data.get("serial") or {}
            with connection_lock:
                stored_port = last_used_serial_settings.get("port")
                stored_baud = last_used_serial_settings.get("baudrate", 9600)
                existing_ser = (
                    serial_conn if serial_conn and serial_conn.is_open else None
                )
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
                    abort_event=execution_abort,
                )
            except Exception as exc:
                yield stream_json_line(
                    {
                        "type": "error",
                        "msg": f"Failed to open serial port {port}: {exc}",
                    }
                )
                return False
            if not ser:
                yield stream_json_line(
                    {
                        "type": "error",
                        "msg": f"Failed to open serial port {port}: device not responding.",
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
            if target_device and not _hostname_matches_target(target_device, hostname):
                if skip_hostname_check:
                    yield stream_json_line(
                        {
                            "type": "progress",
                            "msg": f"⚠ Warning: Selected device is '{target_device}', but connected device is '{hostname}'. Continuing anyway (logs saved under '{target_device}').",
                        }
                    )
                else:
                    logout_close_connection(ser)
                    yield stream_json_line(
                        {
                            "type": "error",
                            "error_code": "HOSTNAME_MISMATCH",
                            "msg": f"Selected device is '{target_device}', but connected device is '{hostname}'. Collection stopped.",
                        }
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
                yield stream_json_line(
                    {"type": "progress", "msg": f"Running '{cmd}'..."}
                )
                try:
                    output = send_command(local_ser, cmd, timeout=30)
                    file_path = save_output_to_file(
                        cmd,
                        output,
                        classroom=classroom,
                        tutor_name=tutor_name,
                        time_slot=time_slot,
                        student_id=student_id,
                        hostname=target_device or hostname,
                        base_dir=base_path,
                    )
                    _save_output_to_engine_students(
                        cmd,
                        output,
                        classroom,
                        tutor_name,
                        time_slot,
                        student_id,
                        target_device or hostname,
                    )
                    files_written.append(file_path)
                    completed += 1
                    pct = (
                        round((completed / total_commands) * 100)
                        if total_commands
                        else 100
                    )
                    yield stream_json_line(
                        {
                            "type": "progress",
                            "msg": f"Completed '{cmd}'.",
                            "cmd_done": True,
                            "progress_pct": pct,
                        }
                    )
                except Exception as exc:
                    del_partial_logs(base_path, hostname)
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
                active_client = (
                    ssh_client if _is_ssh_client_active(ssh_client) else None
                )
                cached_host = last_used_ssh_credentials.get("host")
                cached_user = last_used_ssh_credentials.get("username")
                cached_port = last_used_ssh_credentials.get("port")
                stored_hostname = ssh_hostname or ssh_payload.get("host")
            host = ssh_payload.get("host") or cached_host
            username = ssh_payload.get("username") or cached_user
            password = ssh_payload.get("password") or last_used_ssh_credentials.get(
                "password"
            )
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
                    yield stream_json_line(
                        {"type": "error", "msg": "SSH connection failed."}
                    )
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
                if target_device and not _hostname_matches_target(
                    target_device, hostname
                ):
                    if skip_hostname_check:
                        yield stream_json_line(
                            {
                                "type": "progress",
                                "msg": f"⚠ Warning: Selected device is '{target_device}', but connected device is '{hostname}'. Continuing anyway (logs saved under '{target_device}').",
                            }
                        )
                    else:
                        try:
                            existing_shell = getattr(client, "_shell", None)
                            if existing_shell:
                                existing_shell.close()
                        except Exception:
                            pass
                        try:
                            client.close()
                        except Exception:
                            pass
                        yield stream_json_line(
                            {
                                "type": "error",
                                "error_code": "HOSTNAME_MISMATCH",
                                "msg": f"Selected device is '{target_device}', but connected device is '{hostname}'. Collection stopped.",
                            }
                        )
                        return False
                    _update_ssh_state(
                        client, host, username, password, hostname, resolved_port
                    )

            if not reuse:
                if target_device and not _hostname_matches_target(
                    target_device, hostname
                ):
                    if skip_hostname_check:
                        yield stream_json_line(
                            {
                                "type": "progress",
                                "msg": f"⚠ Warning: Selected device is '{target_device}', but connected device is '{hostname}'. Continuing anyway (logs saved under '{target_device}').",
                            }
                        )
                    else:
                        yield stream_json_line(
                            {
                                "type": "error",
                                "error_code": "HOSTNAME_MISMATCH",
                                "msg": f"Selected device is '{target_device}', but connected device is '{hostname}'. Collection stopped.",
                            }
                        )
                        return False
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
                yield stream_json_line(
                    {"type": "progress", "msg": f"Running '{cmd}'..."}
                )
                try:
                    output = send_command_remote(active, cmd, timeout=30)
                    file_path = save_output_to_file(
                        cmd,
                        output,
                        classroom=classroom,
                        tutor_name=tutor_name,
                        time_slot=time_slot,
                        student_id=student_id,
                        hostname=target_device or hostname,
                        base_dir=base_path,
                    )
                    _save_output_to_engine_students(
                        cmd,
                        output,
                        classroom,
                        tutor_name,
                        time_slot,
                        student_id,
                        target_device or hostname,
                    )
                    files_written.append(file_path)
                    completed += 1
                    pct = (
                        round((completed / total_commands) * 100)
                        if total_commands
                        else 100
                    )
                    yield stream_json_line(
                        {
                            "type": "progress",
                            "msg": f"Completed '{cmd}'.",
                            "cmd_done": True,
                            "progress_pct": pct,
                        }
                    )
                except Exception as exc:
                    del_partial_logs(base_path, hostname)
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
                del_partial_logs(base_path, hostname)
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
        return (
            jsonify({"status": "error", "message": "Thresholds must be integers."}),
            400,
        )

    if major_threshold < 1 or minor_threshold < 1:
        return (
            jsonify({"status": "error", "message": "Thresholds must be at least 1."}),
            400,
        )

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


@app.route("/api/rubric_rules/reset", methods=["POST"])
def api_reset_rubric_rules():
    try:
        rules = reset_rubric_rules()
        return jsonify({"status": "ok", "rules": rules})
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


@app.route("/api/error_context", methods=["POST"])
def api_error_context():
    data = request.get_json() or {}
    target_path = data.get("target_path")
    student_id = (data.get("student_id") or "").strip()
    template_name = (data.get("template_name") or "").strip()
    hostname = (data.get("hostname") or "").strip()
    feature = (data.get("feature") or "").strip()
    expected = data.get("expected")
    actual = data.get("actual")

    if not all([target_path, student_id, template_name, hostname, feature]):
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "Missing target_path, student_id, template_name, hostname, or feature.",
                }
            ),
            400,
        )

    session_dir = Path(target_path).resolve()
    if not session_dir.is_dir():
        return jsonify({"status": "error", "message": "Session path not found."}), 404

    safe_session_dir = _safe_resolve_child(DOCS_DIR, session_dir)
    if not safe_session_dir:
        return jsonify({"status": "error", "message": "Invalid session path."}), 400

    student_config_path = _safe_resolve_child(
        safe_session_dir, safe_session_dir / student_id / hostname / "config.json"
    )
    template_config_path = _safe_resolve_child(
        TEMPLATES_DIR, TEMPLATES_DIR / template_name / hostname / "config.json"
    )
    student_log_dir = _safe_resolve_child(
        safe_session_dir, safe_session_dir / student_id / hostname
    )
    template_log_dir = _safe_resolve_child(
        TEMPLATES_DIR, TEMPLATES_DIR / template_name / hostname / "logs"
    )

    template_config = (
        _load_json_file(template_config_path)
        if template_config_path and template_config_path.exists()
        else {}
    )
    student_config = (
        _load_json_file(student_config_path)
        if student_config_path and student_config_path.exists()
        else {}
    )
    command_hint = _command_hint_for_feature(feature)
    template_raw_path = _find_log_file(template_log_dir, command_hint)
    student_raw_path = _find_log_file(student_log_dir, command_hint)

    context = _extract_error_context(
        template_config, student_config, feature, expected=expected, actual=actual
    )
    template_raw_excerpt = _extract_raw_excerpt(
        template_raw_path, feature, expected=expected, actual=actual
    )
    student_raw_excerpt = _extract_raw_excerpt(
        student_raw_path, feature, expected=expected, actual=actual
    )

    return jsonify(
        {
            "status": "ok",
            "feature": feature,
            "hostname": hostname,
            "student_id": student_id,
            "template_name": template_name,
            "context_path": context["context_path"],
            "highlight_key": context["highlight_key"],
            "template_context": context["template_context"],
            "student_context": context["student_context"],
            "template_config_path": (
                str(template_config_path)
                if template_config_path and template_config_path.exists()
                else None
            ),
            "student_config_path": (
                str(student_config_path)
                if student_config_path and student_config_path.exists()
                else None
            ),
            "command_hint": command_hint,
            "template_raw_path": (
                str(template_raw_path)
                if template_raw_path and template_raw_path.exists()
                else None
            ),
            "student_raw_path": (
                str(student_raw_path)
                if student_raw_path and student_raw_path.exists()
                else None
            ),
            "template_raw_excerpt": template_raw_excerpt,
            "student_raw_excerpt": student_raw_excerpt,
        }
    )


@app.route("/api/melbourne/send", methods=["POST"])
def api_melbourne_send():
    return (
        jsonify(
            {
                "status": "error",
                "message": "Send to Melbourne backend endpoint is not implemented yet.",
            }
        ),
        501,
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

    return jsonify(
        {
            "status": "ok",
            "template": template_name,
            "devices_meta": devices_meta,
            "logs_by_command": logs_by_command,
        }
    )


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
            "exams": _list_existing_exams(),
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
        return (
            jsonify(
                {"status": "error", "message": "Refusing to delete Documents root."}
            ),
            400,
        )

    if len(target.parts) >= len(DOCS_DIR.parts) + 3:
        relative = target.relative_to(DOCS_DIR)
        if len(relative.parts) == 3:
            session_dir = DOCS_DIR / relative.parts[0] / relative.parts[1]
            names = _load_session_student_names(session_dir)
            if relative.parts[2] in names:
                names.pop(relative.parts[2], None)
                _save_session_student_names(session_dir, names)

    _delete_engine_student_logs_for_docs_target(target)
    shutil.rmtree(target)
    return jsonify({"status": "ok", "message": f"Deleted {target}"})


@app.route("/api/admin/sync_mirror", methods=["POST"])
def api_admin_sync_mirror():
    """Remove engine/students dirs whose corresponding Documents folders no longer exist."""
    removed = []
    if not ENGINE_STUDENTS_DIR.exists():
        return jsonify({"status": "ok", "message": "Nothing to sync.", "removed": []})

    for exam_dir in list(ENGINE_STUDENTS_DIR.iterdir()):
        if not exam_dir.is_dir():
            continue
        docs_exam = DOCS_DIR / exam_dir.name
        if not docs_exam.exists():
            shutil.rmtree(exam_dir)
            removed.append(exam_dir.name)
            continue
        for session_dir in list(exam_dir.iterdir()):
            if not session_dir.is_dir():
                continue
            docs_session = docs_exam / session_dir.name
            if not docs_session.exists():
                shutil.rmtree(session_dir)
                removed.append(f"{exam_dir.name}/{session_dir.name}")
                continue
            for student_dir in list(session_dir.iterdir()):
                if not student_dir.is_dir():
                    continue
                docs_student = docs_session / student_dir.name
                if not docs_student.exists():
                    shutil.rmtree(student_dir)
                    removed.append(
                        f"{exam_dir.name}/{session_dir.name}/{student_dir.name}"
                    )

    if removed:
        msg = f"Removed {len(removed)} orphaned mirror folder(s):\n" + "\n".join(
            removed
        )
    else:
        msg = "All mirror folders are in sync. Nothing to remove."
    return jsonify({"status": "ok", "message": msg, "removed": removed})


@app.route("/api/add_student", methods=["POST"])
def api_add_student():
    data = request.get_json() or {}
    session_path = _expand_path(data.get("session_path"))
    student_id = (data.get("student_id") or "").strip()
    student_name = (data.get("student_name") or "").strip()

    if not session_path or not student_id:
        return (
            jsonify(
                {"status": "error", "message": "Missing session_path or student_id."}
            ),
            400,
        )

    session_dir = Path(session_path)
    if not session_dir.exists() or not session_dir.is_dir():
        return jsonify({"status": "error", "message": "Session path not found."}), 404

    docs_dir = (Path.home() / "Documents").resolve()
    target = _safe_resolve_child(docs_dir, session_dir)
    if not target:
        return jsonify({"status": "error", "message": "Invalid session path."}), 400

    student_dir = session_dir / student_id
    student_dir.mkdir(parents=True, exist_ok=True)
    names = _load_session_student_names(session_dir)
    if student_name:
        names[student_id] = student_name
    existing_name = names.get(student_id, "")
    _save_session_student_names(session_dir, names)

    parts = student_dir.parts
    classroom = parts[-4] if len(parts) >= 4 else ""
    tutor_name = parts[-3] if len(parts) >= 3 else ""
    time_slot = parts[-2] if len(parts) >= 2 else ""
    return jsonify(
        {
            "status": "ok",
            "message": f"Student directory created: {student_dir}",
            "path": str(student_dir),
            "classroom": classroom,
            "tutor_name": tutor_name,
            "time_slot": time_slot,
            "exam_name": classroom,
            "session_id": tutor_name,
            "student_id": student_id,
            "student_name": student_name or existing_name,
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


from comparison_engine.compare_main import grading_pipeline


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

    def _student_has_collected_data(student_dir: Path) -> bool:
        if not student_dir.is_dir():
            return False
        for child in student_dir.iterdir():
            if not child.is_dir() or child.name == "results":
                continue
            if (child / "config.json").exists():
                return True
            try:
                if find_show_run_file(str(child)):
                    return True
            except Exception:
                continue
        return False

    skipped_students = []

    for student_entry in sorted(target.iterdir()):
        if not student_entry.is_dir():
            continue
        student_id = student_entry.name
        if not _student_has_collected_data(student_entry):
            skipped_students.append(student_id)
            continue
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

            parsed_file = (
                student_results_dir_student / f"{hostname}_student_parsed.json"
            )
            with open(parsed_file, "w") as handle:
                json.dump(student_config, handle, indent=4)

            result_payload = {
                "student_id": student_id,
                "template_name": template_name,
                "grading_mode": "strict",
                "hostname": hostname,
                "student_show_run_file": show_run_file,
                "student_config_file": (
                    str(student_config_path) if student_config_path.exists() else None
                ),
                "student_parsed_file": str(parsed_file),
                "results": results,
            }

            student_result_file = (
                student_results_dir_student / f"{hostname}_result.json"
            )
            with open(student_result_file, "w") as handle:
                json.dump(result_payload, handle, indent=4)

        summary_file_student = student_results_dir_student / "summary.json"
        with open(summary_file_student, "w") as handle:
            json.dump(summary, handle, indent=4)

        results_summary.append(
            {"student_id": student_id, "status": "Graded", "template": template_name}
        )

    if not results_summary:
        return (
            [],
            "No collected student logs found in this session. Select a student and collect logs before grading.",
        )

    if skipped_students:
        return (
            results_summary,
            f"Grading completed for {len(results_summary)} student(s). "
            f"Skipped {len(skipped_students)} student(s) with no collected logs.",
        )

    return results_summary, "Grading completed."


@app.route("/api/grade", methods=["POST"])
def api_run_grading():
    data = request.get_json() or {}
    classroom = data.get("classroom") or data.get("exam_name")
    tutor_name = data.get("tutor_name") or data.get("session_id")
    time_slot = data.get("time_slot")
    target_path = data.get("target_path")
    template_name = data.get("template_name")
    include_reports = bool(data.get("include_reports"))

    if not target_path:
        return jsonify({"status": "error", "message": "Missing arguments"}), 400

    try:
        # Determine template to use
        available_templates = []
        if TEMPLATES_DIR.is_dir():
            available_templates = [
                p.name for p in TEMPLATES_DIR.iterdir() if p.is_dir()
            ]

        chosen_template = template_name
        if not chosen_template:
            if len(available_templates) == 1:
                chosen_template = available_templates[0]
            else:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": "Multiple templates available. Please select a template.",
                            "templates": available_templates,
                        }
                    ),
                    400,
                )

        summary_results, message = _grade_session_from_config(
            target_path, chosen_template
        )

        if not summary_results:
            return jsonify({"status": "error", "message": message}), 400

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

    class _SuppressDevServerWarning(logging.Filter):
        def filter(self, record):
            message = record.getMessage()
            return (
                "This is a development server. Do not use it in a production deployment."
                not in message
            )

    logging.getLogger("werkzeug").addFilter(_SuppressDevServerWarning())
    print("[*] Running Flask server on http://127.0.0.1:5050")
    app.run(host="127.0.0.1", port=5050, threaded=True)
