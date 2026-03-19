// popup.js - Centaur Brain
const API_BASE = "http://localhost:8080"; // Local FastAPI port

document.addEventListener('DOMContentLoaded', async () => {
  const btnCapture = document.getElementById('btn-preview'); 
  const loading = document.getElementById('loading');
  const mainArea = document.getElementById('main-area');
  const statusArea = document.getElementById('preview-area'); 
  const statusMessage = document.createElement('div');
  
  statusArea.innerHTML = ''; 
  statusArea.appendChild(statusMessage);

  btnCapture.textContent = "Extract to Obsidian";

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
      let extractedTitle = currentTab.title || "";
      let authorHint = "";

      // Handle PDF vs YouTube vs Standard Web Page
      if (url.toLowerCase().split('?')[0].endsWith('.pdf')) {
        statusMessage.textContent = "PDF detected. Sending to backend for processing...";
      } else if (url.includes("youtube.com/watch")) {
        statusMessage.textContent = "Fetching YouTube transcript...";
        
        // 1. Extract and fetch transcript metadata from the page context
        const [{ result }] = await chrome.scripting.executeScript({
          target: { tabId: currentTab.id },
          world: "MAIN",
          func: async () => {
            console.error("CENTAUR DEBUG: Starting extraction v2.2...");
            try {
              // Priority 1: Deep Scrape DOM
              const deepScrape = () => {
                const results = [];
                // Search for segments in standard DOM and common containers
                const selectors = [
                  'ytd-transcript-segment-renderer', 
                  '.segment-text', 
                  '#segments-container yt-formatted-string'
                ];
                
                for (const sel of selectors) {
                  const elements = document.querySelectorAll(sel);
                  if (elements.length > 5) {
                    console.error(`CENTAUR DEBUG: Found ${elements.length} via ${sel}`);
                    return Array.from(elements).map(e => e.innerText.replace(/\s+/g, " ")).join(" ");
                  }
                }
                return null;
              };

              const domTranscript = deepScrape();
              if (domTranscript && domTranscript.length > 100) {
                console.error("CENTAUR DEBUG: Successfully scraped from DOM.");
                return { transcript: domTranscript };
              }

              // Priority 2: Player Data
              const playerResponse = window.ytInitialPlayerResponse || 
                                     (window._yt_player && window._yt_player.initialPlayerResponse);
              
              if (!playerResponse) {
                console.error("CENTAUR DEBUG: Player data missing from window.");
                return { error: "No player data. Please open the 'Show transcript' panel on YouTube first, then try again." };
              }
              
              const tracks = playerResponse.captions?.playerCaptionsTracklistRenderer?.captionTracks;
              if (!tracks || tracks.length === 0) {
                console.error("CENTAUR DEBUG: No tracks in playerResponse.");
                return { error: "No captions found. Please click 'Show transcript' on the video page." };
              }
              
              const track = tracks.sort((a, b) => {
                const aCode = (a.languageCode || "").toLowerCase();
                const bCode = (b.languageCode || "").toLowerCase();
                if (aCode === "en") return -1;
                if (bCode === "en") return 1;
                return (aCode.startsWith("en") ? -1 : 1);
              })[0];
              
              console.error("CENTAUR DEBUG: Selected track URL:", track.baseUrl);
              
              // Brute force fetch with varied credentials
              const formats = ["json3", "srv1"];
              for (const fmt of formats) {
                const url = new URL(track.baseUrl);
                url.searchParams.set("fmt", fmt);
                
                for (const cred of ['include', 'omit']) {
                  try {
                    console.error(`CENTAUR DEBUG: Fetching ${fmt} with ${cred}...`);
                    const res = await fetch(url.toString(), { credentials: cred });
                    const body = await res.text();
                    
                    if (body && body.length > 100) {
                      if (fmt === "json3" || body.startsWith("{")) {
                        const data = JSON.parse(body);
                        const text = data.events?.filter(e => e.segs).map(e => e.segs.map(s => s.utf8).join("")).join(" ");
                        if (text && text.length > 100) return { transcript: text };
                      } else {
                        const matches = body.match(/<text.*?>([\s\S]*?)<\/text>/gi);
                        if (matches) {
                          const text = matches.map(m => m.replace(/<text[^>]*>/i, "").replace(/<\/text>/i, "").replace(/&(#?[a-zA-Z0-9]+);/g, (m, e) => {
                            const ent = { 'amp': '&', 'lt': '<', 'gt': '>', 'quot': '"', 'apos': "'", '#39': "'" };
                            return ent[e] || m;
                          })).join(" ");
                          if (text.length > 100) return { transcript: text };
                        }
                      }
                    }
                  } catch (e) { console.error(`CENTAUR DEBUG: Fetch error:`, e); }
                }
              }
              
              return { baseUrl: track.baseUrl };
            } catch (e) {
              console.error("CENTAUR DEBUG: Fatal:", e);
              return { error: "Failed to extract transcript: " + e.message };
            }
          }
        });

        if (result.error) throw new Error(result.error);
        
        if (result.transcript) {
          markdownText = result.transcript;
        } else if (result.baseUrl) {
          // 2. Fetch via background script as a fallback
          const ytResponse = await chrome.runtime.sendMessage({ 
            action: "getYouTubeTranscript", 
            baseUrl: result.baseUrl 
          });
          
          if (ytResponse.error) throw new Error(ytResponse.error);
          markdownText = ytResponse.transcript;
        } else {
          throw new Error("Could not find a valid transcript.");
        }
      } else {
        statusMessage.textContent = "Extracting article text...";
        try {
          const extraction = await chrome.tabs.sendMessage(currentTab.id, { action: "extractContent" });
          if (extraction) {
            if (extraction.markdown) markdownText = extraction.markdown;
            if (extraction.title) extractedTitle = extraction.title;
            if (extraction.byline) authorHint = extraction.byline;
            if (extraction.error) console.error("Extraction error:", extraction.error);
          }
          
          if (!markdownText) {
            console.log("Extraction returned no content, falling back to backend fetch.");
          }
        } catch (e) {
          console.log("Content script connection failed (try refreshing the page):", e);
          markdownText = "";
        }
      }

      // Send to Backend
      statusMessage.textContent = "Generating Brain Node & Archiving...";
      const response = await fetch(`${API_BASE}/process`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ 
          source: "extension", 
          url: url, 
          markdownText: markdownText,
          title: extractedTitle || "Untitled",
          authorHint: authorHint || ""
        })
      });

      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "API Error");

      statusMessage.innerHTML = `<p style="color: #4ade80; font-weight: bold;">✓ Sent to Obsidian Inbox!</p>`;
      setTimeout(() => window.close(), 2500);

    } catch (err) {
      statusMessage.innerHTML = `<p style="color: #f87171;">Error: ${err.message}</p>`;
      btnCapture.style.display = 'block';
      btnCapture.textContent = "Try Again";
    } finally {
      loading.style.display = 'none';
    }
  });
});
