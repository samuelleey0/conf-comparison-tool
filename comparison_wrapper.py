import os
import json
from comparsion_engine.parser import parse_device_logs
from comparsion_engine.template_manager import choose_show_run_file

def handle_template_upload(files, form_data, base_dir):
    """
    Handles extracting uploaded logs from Device Setup, saving them into the 
    templates directory, and running the parser to generate the baseline config.
    """
    template_name = form_data.get("template_name", "default")
    devices_meta_str = form_data.get("devices_meta", "{}")
    
    try:
        devices_meta = json.loads(devices_meta_str)
    except json.JSONDecodeError:
        return {"status": "error", "message": "Invalid devices metadata format."}

    template_dir = os.path.join(base_dir, "comparsion_engine", "templates", template_name)
    
    results = {}

    for hostname, commands in devices_meta.items():
        hostname_logs_dir = os.path.join(template_dir, hostname, "logs")
        os.makedirs(hostname_logs_dir, exist_ok=True)
        
        saved_log_paths = []
        
        for cmd in commands:
            # Field name from frontend: file_HOSTNAME_COMMAND
            field_name = f"file_{hostname}_{cmd}"
            if field_name in files:
                file_obj = files[field_name]
                if file_obj.filename:
                    # Sanitize filename somewhat, or use default log extension
                    safe_cmd = cmd.replace(" ", "_").replace("/", "_")
                    filename = f"{safe_cmd}.txt"
                    file_path = os.path.join(hostname_logs_dir, filename)
                    
                    if file_obj.filename.lower().endswith(".docx"):
                        import docx2txt
                        import tempfile
                        with tempfile.NamedTemporaryFile(delete=False, suffix=".docx") as tmp:
                            file_obj.save(tmp.name)
                            tmp_path = tmp.name
                        try:
                            text = docx2txt.process(tmp_path)
                            with open(file_path, "w", encoding="utf-8") as f:
                                f.write(text)
                        finally:
                            if os.path.exists(tmp_path):
                                os.remove(tmp_path)
                    else:
                        file_obj.save(file_path)
                        
                    saved_log_paths.append(file_path)

        if not saved_log_paths:
            continue

        # Use the comparator's logic to figure out which one is the show run
        try:
            show_run_path = choose_show_run_file(saved_log_paths)
            
            # Parse all uploaded logs to build the "perfect" config
            template_config = parse_device_logs(saved_log_paths)
            
            config_json_path = os.path.join(template_dir, hostname, "config.json")
            with open(config_json_path, "w") as target_file:
                json.dump(template_config, target_file, indent=4)
                
            # Save manifest
            manifest_path = os.path.join(template_dir, hostname, "logs.json")
            with open(manifest_path, "w") as manifest_file:
                json.dump({
                    "hostname": hostname,
                    "show_run_file": os.path.basename(show_run_path),
                    "logs": [os.path.basename(p) for p in saved_log_paths]
                }, manifest_file, indent=4)
                
            results[hostname] = "Success"
        except Exception as e:
            results[hostname] = f"Error parsing: {str(e)}"

    return {"status": "success", "results": results}
