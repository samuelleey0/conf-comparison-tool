async function connectSerial() {
  const res = await fetch("http://127.0.0.1:5050/serial/connect", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ port: "/dev/ttyUSB0" }), // change if needed
  });
  const data = await res.json();
  document.getElementById("output").innerText = JSON.stringify(data, null, 2);
}

async function runSerialCommands() {
  const res = await fetch("http://127.0.0.1:5050/serial/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      port: "/dev/ttyUSB0",
      commands: ["show ip interface brief", "show running-config"],
    }),
  });
  const data = await res.json();
  document.getElementById("output").innerText = JSON.stringify(data, null, 2);
}

async function runSSHCommands() {
  const res = await fetch("http://127.0.0.1:5050/ssh/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      host: "192.168.1.1",      // change to your device IP
      username: "admin",        // update credentials
      password: "cisco",
      commands: ["show ip interface brief", "show version"],
    }),
  });
  const data = await res.json();
  document.getElementById("output").innerText = JSON.stringify(data, null, 2);
}