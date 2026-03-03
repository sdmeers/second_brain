// background.js - Centaur Notes
// Handles YouTube Transcript extraction and Backend Communication

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === "getYouTubeTranscript") {
    fetchYouTubeTranscript(request.baseUrl)
      .then(transcript => sendResponse({ transcript }))
      .catch(error => sendResponse({ error: error.message }));
    return true; // Keep channel open for async response
  }
});

async function fetchYouTubeTranscript(baseUrl) {
  try {
    const formats = ["json3", "srv1", "srv3"];
    let lastError = null;

    for (const fmt of formats) {
      const fetchUrl = new URL(baseUrl);
      fetchUrl.searchParams.set("fmt", fmt);
      
      // Try with different fetch options to bypass ad-blockers or signature issues
      const configs = [
        { credentials: 'include' },
        { credentials: 'omit' },
        { mode: 'no-cors' } // unlikely to help with text but worth a try as a desperate measure
      ];

      for (const config of configs) {
        try {
          console.log(`Background: Trying ${fmt} with ${JSON.stringify(config)}`);
          const response = await fetch(fetchUrl.toString(), config);
          if (!response.ok && config.mode !== 'no-cors') continue;

          const body = await response.text();
          if (!body || body.trim().length === 0) continue;

          // JSON Parsing
          if (fmt === "json3" || body.trim().startsWith("{")) {
            try {
              const data = JSON.parse(body);
              if (data && data.events) {
                const text = data.events.filter(e => e.segs).map(e => e.segs.map(s => s.utf8).join("")).join(" ");
                if (text.trim().length > 0) return text.replace(/\s+/g, " ").trim();
              }
            } catch (e) { /* continue */ }
          }

          // XML Parsing
          const textMatches = body.match(/<text.*?>([\s\S]*?)<\/text>/gi);
          if (textMatches) {
            return textMatches
              .map(m => {
                const content = m.replace(/<text[^>]*>/i, "").replace(/<\/text>/i, "");
                return decodeHTMLEntities(content);
              })
              .filter(t => t.length > 0)
              .join(" ")
              .replace(/\s+/g, " ")
              .trim();
          }
        } catch (e) {
          lastError = e;
          console.warn(`Background ${fmt} config failed:`, e);
        }
      }
    }
    throw lastError || new Error("All transcript fetch attempts failed.");
  } catch (error) {
    console.error("Final Background Fetch Error:", error);
    throw error;
  }
}

function decodeHTMLEntities(text) {
  if (!text) return "";
  return text.replace(/&(#?[a-zA-Z0-9]+);/g, (match, entity) => {
    const entities = {
      'amp': '&', 'lt': '<', 'gt': '>', 'quot': '"', 'apos': "'", '#39': "'"
    };
    if (entities[entity]) return entities[entity];
    if (entity.startsWith('#')) {
      const code = entity.startsWith('#x') 
        ? parseInt(entity.slice(2), 16) 
        : parseInt(entity.slice(1), 10);
      return isNaN(code) ? match : String.fromCharCode(code);
    }
    return match;
  });
}
