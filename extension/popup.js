// popup.js - Centaur Notes
const API_BASE = "http://localhost:8080"; // Default Cloud Run Local port

document.addEventListener('DOMContentLoaded', async () => {
  const btnCapture = document.getElementById('btn-preview'); // Reusing ID for simplicity
  const loading = document.getElementById('loading');
  const mainArea = document.getElementById('main-area');
  const statusArea = document.getElementById('preview-area'); // Reusing ID for status display
  const statusMessage = document.createElement('div');
  
  statusArea.innerHTML = ''; // Clear preview area
  statusArea.appendChild(statusMessage);

  btnCapture.textContent = "Capture to Notion";

  btnCapture.addEventListener('click', async () => {
    loading.style.display = 'block';
    btnCapture.style.display = 'none';
    statusMessage.textContent = "Extracting content...";

    try {
      const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
      if (!tabs || tabs.length === 0) throw new Error("No active tab.");
      const currentTab = tabs[0];
      
      let markdownText = "";
      let url = currentTab.url;

      // Handle YouTube vs. Standard Web Page
      if (url.includes("youtube.com/watch")) {
        statusMessage.textContent = "Fetching YouTube transcript...";
        const videoId = new URL(url).searchParams.get("v");
        const ytResponse = await chrome.runtime.sendMessage({ action: "getYouTubeTranscript", videoId });
        if (ytResponse.error) throw new Error(ytResponse.error);
        markdownText = ytResponse.transcript;
      } else {
        statusMessage.textContent = "Extracting article text...";
        try {
          const extraction = await chrome.tabs.sendMessage(currentTab.id, { action: "extractContent" });
          if (extraction && extraction.markdown) {
            markdownText = extraction.markdown;
          } else {
            console.log("Extraction returned no content, falling back to backend fetch.");
            markdownText = ""; 
          }
        } catch (e) {
          console.log("Content script connection failed, falling back to backend fetch.");
          markdownText = "";
        }
      }

      // Send to Backend
      statusMessage.textContent = "AI Analysis & Saving to Notion...";
      const response = await fetch(`${API_BASE}/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ 
          source: "extension", 
          url: url, 
          markdownText: markdownText 
        })
      });

      const data = await response.json();
      if (data.error) throw new Error(data.error);

      statusMessage.innerHTML = `<p style="color: green; font-weight: bold;">✓ Successfully saved to Notion!</p>`;
      setTimeout(() => window.close(), 2000);

    } catch (err) {
      statusMessage.innerHTML = `<p style="color: red;">Error: ${err.message}</p>`;
      btnCapture.style.display = 'block';
      btnCapture.textContent = "Try Again";
    } finally {
      loading.style.display = 'none';
    }
  });
});
