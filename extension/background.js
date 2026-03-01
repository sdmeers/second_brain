// background.js - Centaur Notes
// Handles YouTube Transcript extraction and Backend Communication

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === "getYouTubeTranscript") {
    fetchYouTubeTranscript(request.videoId)
      .then(transcript => sendResponse({ transcript }))
      .catch(error => sendResponse({ error: error.message }));
    return true; // Keep channel open for async response
  }
});

async function fetchYouTubeTranscript(videoId) {
  try {
    // 1. Fetch the YouTube video page
    const response = await fetch(`https://www.youtube.com/watch?v=${videoId}`);
    const html = await response.text();

    // 2. Extract the captions URL from the page's initial data JSON
    const captionsMatch = html.match(/"captionTracks":\[(.*?)\]/);
    if (!captionsMatch) throw new Error("Could not find captions for this video.");
    
    const captions = JSON.parse(`[${captionsMatch[1]}]`);
    const englishCaptions = captions.find(c => c.languageCode === 'en' || c.languageCode === 'en-US') || captions[0];
    
    if (!englishCaptions) throw new Error("No English captions available.");

    // 3. Fetch and parse the XML captions
    const transcriptResponse = await fetch(englishCaptions.baseUrl);
    const transcriptXml = await transcriptResponse.text();
    
    // Simple XML text extraction (regex approach for speed/minimal overhead)
    const textMatches = transcriptXml.match(/<text.*?>(.*?)<\/text>/g);
    if (!textMatches) throw new Error("Could not parse transcript XML.");

    return textMatches
      .map(match => {
        const content = match.match(/<text.*?>(.*?)<\/text>/)[1];
        return decodeHTMLEntities(content);
      })
      .join(" ");
  } catch (error) {
    console.error("YouTube Transcript Error:", error);
    throw error;
  }
}

function decodeHTMLEntities(text) {
  const entities = {
    '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&#39;': "'"
  };
  return text.replace(/&amp;|&lt;|&gt;|&quot;|&#39;/g, m => entities[m]);
}
