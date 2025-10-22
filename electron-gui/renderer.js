// electron-gui/renderer.js
function goTo(page) {
  window.location.href = page;
}

function saveStudentSession() {
  const studentID = document.getElementById('studentID').value;
  const sessionID = document.getElementById('sessionID').value;
  if (!studentID || !sessionID) {
    alert("Please fill in both Student ID and Session ID");
    return;
  }
  localStorage.setItem('studentID', studentID);
  localStorage.setItem('sessionID', sessionID);
  goTo('connection.html');
}

function saveConnectionType() {
  const connection = document.querySelector('input[name="connectionType"]:checked');
  if (!connection) {
    alert("Select a connection type first");
    return;
  }
  localStorage.setItem('connectionType', connection.value);
  goTo('commands.html');
}

function startExecution() {
  const selectedCommands = Array.from(document.querySelectorAll('input[name="command"]:checked'))
    .map(cmd => cmd.value);
  if (selectedCommands.length === 0) {
    alert("Please select at least one command");
    return;
  }
  localStorage.setItem('commands', JSON.stringify(selectedCommands));
  goTo('execution.html');
}