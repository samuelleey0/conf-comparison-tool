def compare_dicts(template: dict, student: dict, parent_key="") -> list:
    """
    Recursively compares two dictionaries and returns structured results.
    """
    results = []

    for key, t_val in template.items():
        full_key = f"{parent_key}.{key}" if parent_key else key
        s_val = student.get(key)

        if s_val is None:
            results.append(
                {
                    "feature": full_key,
                    "expected": t_val,
                    "actual": None,
                    "status": "missing",
                }
            )
        elif isinstance(t_val, dict):
            results.extend(compare_dicts(t_val, s_val, full_key))
        elif isinstance(t_val, list):
            # compare lists of dicts (users)
            t_users = {u["username"]: u for u in t_val}
            s_users = {u["username"]: u for u in s_val}
            for uname, udata in t_users.items():
                if uname not in s_users:
                    results.append(
                        {"feature": f"{full_key}.{uname}", "status": "missing"}
                    )
                else:
                    if udata.get("privilege") != s_users[uname].get("privilege"):
                        results.append(
                            {
                                "feature": f"{full_key}.{uname}.privilege",
                                "expected": udata.get("privilege"),
                                "actual": s_users[uname].get("privilege"),
                                "status": "mismatch",
                            }
                        )
        else:
            if t_val != s_val:
                results.append(
                    {
                        "feature": full_key,
                        "expected": t_val,
                        "actual": s_val,
                        "status": "mismatch",
                    }
                )
            else:
                results.append({"feature": full_key, "status": "correct"})

    # Detect extra fields in student config
    for key in student.keys():
        full_key = f"{parent_key}.{key}" if parent_key else key
        if key not in template:
            results.append(
                {
                    "feature": full_key,
                    "expected": None,
                    "actual": student[key],
                    "status": "extra",
                }
            )

    return results
