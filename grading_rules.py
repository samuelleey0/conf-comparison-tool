"""Rubric rules, grading policy, and result classification helpers.

This module is the single source of truth for how raw comparator outcomes become
major/minor/skipped findings. UI pages should change rules through the API
instead of editing config/rubric_rules.json directly.
"""

import json
import re
from pathlib import Path

from grading_dedup import (
    config_block_ref,
    empty_phase1_summary,
    is_verification_feature,
    verification_block_name,
    verification_is_deduplicated,
)

BASE_DIR = Path(__file__).resolve().parent
GRADING_POLICY_PATH = BASE_DIR / "config" / "grading_policy.json"
RUBRIC_RULES_PATH = BASE_DIR / "config" / "rubric_rules.json"

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
            "major",
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
            "minor",
            "Routing protocol network statement matches no interface.",
        ),
        (
            "EXTRA_ROUTING_INSTANCE",
            "major",
            "Multiple routing protocol instances configured.",
        ),
        ("MISMATCH_RIP_VERSION", "major", "RIP version incorrectly set."),
        (
            "MISMATCH_EIGRP_AS_MISMATCH",
            "major",
            "EIGRP AS numbers not common across devices.",
        ),
        ("MISMATCH_EIGRP_AS_INCORRECT", "minor", "EIGRP AS number incorrectly set."),
        ("MISMATCH_ROUTING_PASSIVE", "minor", "Passive interface declaration differs."),
        ("MISMATCH_ROUTING_REDISTRIBUTE", "minor", "Redistribution statement differs."),
        ("MISMATCH_ROUTING_AUTO_SUMMARY", "major", "Auto-summary setting differs."),
        ("MISMATCH_OSPF_AREA_ID_SINGLE", "minor", "Single-area OSPF not using area 0."),
        ("MISMATCH_OSPF_AREA_0_MISSING", "minor", "Multi-area OSPF missing area 0."),
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
            "minor",
            "OSPF network groups not in a single area.",
        ),
        ("MISMATCH_OSPF_WRONG_AREA", "minor", "OSPF network groups in wrong area."),
        (
            "MISSING_OSPF_POINT_TO_POINT",
            "minor",
            "Missing ip ospf network point-to-point command.",
        ),
        ("MISMATCH_STATIC_ROUTE_NETWORK", "major", "Static route destination differs."),
        ("MISMATCH_STATIC_ROUTE_NEXTHOP", "major", "Static route next-hop differs."),
        ("MISMATCH_DEFAULT_ROUTE_NEXTHOP", "major", "Default route next-hop differs."),
        ("EXTRA_STATIC_ROUTE", "minor", "Extra static route added when not required."),
        ("EXTRA_DEFAULT_ROUTE", "minor", "Multiple correct default routes configured."),
        (
            "EXTRA_STATIC_CONFIGURED",
            "major",
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
            "minor",
            "NAT pool range does not cover required range.",
        ),
        ("MISMATCH_NAT_POOL_PREFIX", "minor", "NAT pool prefix incorrect."),
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
        ("EXTRA_NAT_OVERLOAD", "minor", "NAT pool overloaded when it should not be."),
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
            "minor",
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
        ("MISMATCH_CLOCK_RATE", "major", "Serial clock rate differs."),
        ("MISSING_DESCRIPTION", "minor", "Required interface description not set."),
        (
            "EXTRA_DESCRIPTION",
            "minor",
            "Interface description set when it should not be.",
        ),
        (
            "EXTRA_IFACE_CONFIGURED",
            "major",
            "Interface configured when it should not have been.",
        ),
        ("MISMATCH_ENCAPSULATION", "major", "Serial encapsulation type differs."),
        ("EXTRA_PPP_ENABLED", "major", "PPP enabled when it should not be."),
        ("MISMATCH_PPP_AUTH", "minor", "PPP authentication method differs."),
        (
            "MISMATCH_PPP_PROTOCOL_CONFLICT",
            "major",
            "PPP protocol conflict on shared link.",
        ),
        ("MISMATCH_PPP_USERNAME", "minor", "Incorrect PPP username."),
        ("MISMATCH_PPP_PASSWORD", "minor", "Incorrect PPP password."),
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
            "minor",
            "ACL applied to interface when it should not be.",
        ),
        ("NON_APPLIED_ACL", "minor", "ACL configured but not applied anywhere."),
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
        ("MISMATCH_TRUNK_NATIVE_VLAN", "minor", "Trunk native VLAN differs."),
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
            "minor",
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
        ("MISMATCH_LINE_PASSWORD", "minor", "Incorrect password on VTY/console lines."),
        ("EXTRA_LINE_ENABLED", "minor", "Line access enabled when it should not be."),
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
        ("MISSING_DEFAULT_GATEWAY", "minor", "Switch default gateway not configured."),
        ("MISSING_PASSIVE_INTERFACE", "minor", "Passive interface missing."),
        (
            "MISSING_REDISTRIBUTE",
            "major",
            "Default routes not redistributed when required.",
        ),
        ("MISSING_DHCP_POOL", "major", "Required DHCP pool not configured."),
        ("MISSING_DHCP_EXCLUDED", "minor", "DHCP excluded range not configured."),
        ("MISSING_DHCP_EMPTY_POOL", "minor", "DHCP pool created but no addresses."),
        ("MISSING_NAT_INSIDE", "major", "NAT inside not declared on interface."),
        ("MISSING_NAT_OUTSIDE", "major", "NAT outside not declared on interface."),
        ("MISSING_NAT_POOL", "major", "NAT pool absent."),
        ("MISSING_NAT_SOURCE", "major", "NAT inside source binding absent."),
        ("MISSING_NAT_ACL", "minor", "No ACL exists on NAT pool."),
        ("MISSING_NAT_OVERLOAD", "minor", "NAT pool not overloaded when required."),
        ("MISSING_NAT_VALID_POOL", "major", "No valid NAT pool found."),
        ("MISSING_INTERFACE_CONFIG", "major", "Interface entirely unconfigured."),
        ("MISSING_IP", "major", "IP address absent on interface."),
        ("MISSING_CLOCK_RATE", "major", "Clock rate not configured on DCE interface."),
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
            "minor",
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
        ("MISSING_LINE_PASSWORD", "minor", "No password specified on lines."),
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
            "major",
            "Static routes installed when they should be none.",
        ),
        (
            "EXTRA_REDISTRIBUTE",
            "minor",
            "Default routes redistributed when not required.",
        ),
        ("EXTRA_DHCP_POOL", "major", "Unrequired DHCP pool configured."),
        ("EXTRA_DHCP_ADVERTISING", "minor", "DHCP pool advertising when not required."),
        ("EXTRA_NAT_OVERLOAD", "minor", "NAT pool overloaded when it should not be."),
        (
            "EXTRA_NAT_INTERFACE_CONFIGURED",
            "major",
            "NAT configured on interface when not required.",
        ),
        ("EXTRA_NAT_SOURCE", "minor", "Extra NAT inside source binding."),
        (
            "EXTRA_IFACE_CONFIGURED",
            "major",
            "Interface configured when it should not have been.",
        ),
        ("EXTRA_PPP_ENABLED", "major", "PPP enabled when it should not be."),
        ("EXTRA_ACL", "minor", "Extra ACL created."),
        ("EXTRA_ACL_RULE", "minor", "Extra ACL rule added."),
        ("EXTRA_ACL_APPLIED", "minor", "ACL applied to wrong interface."),
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
        ("EXTRA_LINE_ENABLED", "minor", "Line access enabled when it should not be."),
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
        "MISMATCH_ROUTING_PASSIVE": [
            r"\.passive_interface",
            r"^show_running_config\.routing\.rip$",
        ],
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
        "NON_APPLIED_ACL": [r"\.access_lists\..+\.applied$"],
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
        "MISMATCH_VTY_TRANSPORT": [r"\.vty\.transport$", r"\.console\.transport$"],
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


def disable_rubric_rule(rule_code):
    """Disable a rule globally while keeping matching findings visible as skipped."""
    target = str(rule_code or "").strip()
    if not target:
        raise ValueError("Missing rule code.")

    rules = load_rubric_rules()
    matched = None
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        # Match both code and id because custom rules can use either as their stable key.
        if target in {str(rule.get("code") or ""), str(rule.get("id") or "")}:
            rule["enabled"] = False
            matched = rule
            break

    if not matched:
        raise ValueError(f"Rubric rule not found: {target}")

    saved = save_rubric_rules(rules)
    saved_rule = next(
        (
            rule
            for rule in saved
            if target in {str(rule.get("code") or ""), str(rule.get("id") or "")}
        ),
        matched,
    )
    return saved_rule, saved


def enable_rubric_rule(rule_code):
    """Re-enable a globally disabled rule so future result refreshes count it again."""
    target = str(rule_code or "").strip()
    if not target:
        raise ValueError("Missing rule code.")

    rules = load_rubric_rules()
    matched = None
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        # Match both code and id because custom rules can use either as their stable key.
        if target in {str(rule.get("code") or ""), str(rule.get("id") or "")}:
            rule["enabled"] = True
            matched = rule
            break

    if not matched:
        raise ValueError(f"Rubric rule not found: {target}")

    saved = save_rubric_rules(rules)
    saved_rule = next(
        (
            rule
            for rule in saved
            if target in {str(rule.get("code") or ""), str(rule.get("id") or "")}
        ),
        matched,
    )
    return saved_rule, saved


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


def classify_items(items, policy, rubric_rules=None):
    rubric_compiled, rubric_by_code, disabled_compiled, disabled_by_code = (
        _compile_rubric_rules(rubric_rules)
    )

    summary = empty_phase1_summary()

    minor_rule_hits = set()

    config_items = [
        item for item in items if not is_verification_feature(item.get("feature"))
    ]
    verification_items = [
        item for item in items if is_verification_feature(item.get("feature"))
    ]

    classified_config = []
    failed_config_refs = set()
    failed_config_features = set()

    for item in config_items:
        item_copy = _classify_single_item(
            item, rubric_compiled, rubric_by_code, disabled_compiled, disabled_by_code
        )
        item_copy["layer"] = "config"
        item_copy["block_name"] = config_block_ref(item.get("feature")) or item.get(
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
    verification_rule_hits = set()
    for item in verification_items:
        item_copy = _classify_single_item(
            item, rubric_compiled, rubric_by_code, disabled_compiled, disabled_by_code
        )
        item_copy["layer"] = "verification"
        item_copy["block_name"] = item_copy.get(
            "block_name"
        ) or verification_block_name(item.get("feature"))
        deduplicated, layer1_ref = verification_is_deduplicated(
            item.get("feature"), failed_config_refs, failed_config_features
        )
        item_copy["layer1_ref"] = item_copy.get("layer1_ref") or layer1_ref
        item_copy["deduplicated"] = deduplicated
        item_copy["verification_rule_deduplicated"] = False
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
                severity = item_copy.get("severity", "minor")
                rule_id = item_copy.get("rule_id")
                rule_key = item_copy.get("rule_code") or item_copy.get("outcome_code") or rule_id or ""
                verification_hit_key = (
                    item_copy.get("hostname") or "",
                    item_copy.get("block_name") or "",
                    rule_key,
                )
                if rule_key and verification_hit_key in verification_rule_hits:
                    item_copy["verification_rule_deduplicated"] = True
                    item_copy["counts_toward_marking"] = False
                    summary["verify_deduplicated"] += 1
                else:
                    if rule_key:
                        verification_rule_hits.add(verification_hit_key)
                    summary["verify_failed"] += 1
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


def evaluate_pass_fail(summary, policy):
    major_threshold = int(policy.get("major_threshold") or 1)
    minor_threshold = int(policy.get("minor_threshold") or 5)
    failed = summary.get("major", 0) >= major_threshold
    if not failed:
        failed = summary.get("minor", 0) >= minor_threshold
    return not failed
