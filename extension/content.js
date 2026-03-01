chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === "extractContent") {
    try {
      console.log("Cogito: Starting extraction...");
      const documentClone = document.cloneNode(true);
      const reader = new Readability(documentClone);
      const article = reader.parse();

      if (!article || !article.content) {
        console.error("Cogito: Readability failed to find content.");
        sendResponse({ error: "This page doesn't look like an article. Could not extract content." });
        return true;
      }

      const turndownService = new TurndownService();
      const markdown = turndownService.turndown(article.content);

      sendResponse({
        title: article.title,
        markdown: markdown,
        url: window.location.href
      });
    } catch (error) {
      sendResponse({ error: error.message });
    }
  }
  return true;
});
