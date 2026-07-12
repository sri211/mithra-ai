const bg = (msg) => new Promise((res) => chrome.runtime.sendMessage(msg, res));

async function refresh() {
  const dot = document.getElementById("dot");
  const statusText = document.getElementById("statusText");
  const whoBox = document.getElementById("whoBox");
  const connectBtn = document.getElementById("connectBtn");

  const st = await bg({ type: "GET_STATUS" });
  if (st?.connected) {
    dot.className = "dot on";
    statusText.textContent = "Connected";
    whoBox.style.display = "block";
    whoBox.innerHTML = `${st.user.name || ""}<small>${st.user.email || ""} · ${st.user.plan || ""} plan</small>`;
    connectBtn.style.display = "none";
  } else {
    dot.className = "dot off";
    statusText.textContent = st?.expired ? "Session expired — reconnect" : "Not connected";
    whoBox.style.display = "none";
    connectBtn.style.display = "block";
  }
}

document.getElementById("connectBtn").onclick = async () => {
  await bg({ type: "OPEN_CONNECT" });
  document.getElementById("statusText").textContent = "Sign in on the Mithra tab, then reopen this.";
};

refresh();
