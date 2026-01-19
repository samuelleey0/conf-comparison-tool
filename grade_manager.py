import re


class Grader:
    def grade(self, config_text: str, rubric: dict) -> list:
        results = []

        for crit in rubric.get("criteria", []):
            name = crit["name"]
            points = crit.get("points", 0)

            # --- SCENARIO A: Dynamic List (VLANs) ---
            if "dynamic_data" in crit:
                vlan_list = crit["dynamic_data"]
                points_per_vlan = points / len(vlan_list) if vlan_list else 0

                for vlan in vlan_list:
                    v_id = vlan["id"]
                    v_name = vlan["name"]

                    # Search for: vlan <ID> ... name <NAME>
                    pattern = rf"vlan\s+{v_id}[\s\S]*?name\s+{v_name}"
                    match = re.search(
                        pattern, config_text, re.MULTILINE | re.IGNORECASE
                    )

                    status = "MEET" if match else "INCORRECT/MISSING"
                    results.append(
                        {
                            "criterion": f"VLAN {v_id} ({v_name})",
                            "status": status,
                            "points": f"{int(points_per_vlan) if match else 0}/{int(points_per_vlan)}",
                        }
                    )

            # --- SCENARIO B: Single Check (Hostname/IP) ---
            else:
                pattern = crit.get("pattern", "")
                exists_p = crit.get("exists_pattern", "")

                match = re.search(pattern, config_text, re.MULTILINE | re.IGNORECASE)
                if match:
                    status, earned = "MEET", points
                elif exists_p and re.search(
                    exists_p, config_text, re.MULTILINE | re.IGNORECASE
                ):
                    status, earned = "INCORRECT", 0
                else:
                    status, earned = "MISSING", 0

                results.append(
                    {
                        "criterion": name,
                        "status": status,
                        "points": f"{earned}/{points}",
                    }
                )

        return results
