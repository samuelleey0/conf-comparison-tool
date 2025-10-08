from flask import Flask, jsonify, request
from serial_utils import connect_to_serial, send_command, disable_paging, enter_enable_mode, logout_close_connection
from remote_utils import connect_ssh, send_command_ssh, disable_paging_ssh, enter_enable_mode_ssh
import traceback
import time

app = Flask(__name__)

@app.route('/ping', methods=['GET'])
def ping():
    return jsonify({"message": "Server online"})


@app.route('/serial/connect', methods=['POST'])
def serial_connect():
    data = request.get_json()
    port = data.get("port", "/dev/ttyUSB0")
    try:
        ser = connect_to_serial(port)
        if ser:
            return jsonify({"status": "connected", "port": port})
        else:
            return jsonify({"status": "failed", "message": "Unable to connect"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e), "trace": traceback.format_exc()})


@app.route('/serial/run', methods=['POST'])
def serial_run():
    data = request.get_json()
    port = data.get("port", "/dev/ttyUSB0")
    commands = data.get("commands", ["show version"])
    try:
        ser = connect_to_serial(port)
        enter_enable_mode(ser)
        disable_paging(ser)

        all_output = {}
        for cmd in commands:
            output = send_command(ser, cmd, timeout=20)
            all_output[cmd] = output
            time.sleep(1)

        logout_close_connection(ser)
        return jsonify({"status": "success", "results": all_output})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e), "trace": traceback.format_exc()})


@app.route('/ssh/run', methods=['POST'])
def ssh_run():
    data = request.get_json()
    host = data.get("host")
    username = data.get("username")
    password = data.get("password")
    commands = data.get("commands", ["show version"])

    try:
        client, shell = connect_ssh(host, username, password)
        if not client or not shell:
            return jsonify({"status": "failed", "message": "SSH connection failed"})

        enter_enable_mode_ssh(shell)
        disable_paging_ssh(shell)

        all_output = {}
        for cmd in commands:
            output = send_command_ssh(shell, cmd, timeout=20)
            all_output[cmd] = output

        shell.close()
        client.close()

        return jsonify({"status": "success", "results": all_output})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e), "trace": traceback.format_exc()})


if __name__ == '__main__':
    print("[*] Running Cisco Automation Flask server on http://127.0.0.1:5050")
    app.run(port=5050)