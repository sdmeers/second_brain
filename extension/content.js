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

      // Try to find author in meta tags if Readability missed it
      let author = article.byline;
      if (!author) {
        const authorMeta = document.querySelector('meta[name="author"]') || 
                           document.querySelector('meta[property="og:article:author"]') ||
                           document.querySelector('.p-author');
        if (authorMeta) {
          author = authorMeta.content || authorMeta.innerText;
        }
      }

      sendResponse({
        title: article.title,
        byline: author,
        markdown: markdown,
        url: window.location.href
      });
    } catch (error) {
      sendResponse({ error: error.message });
    }
  }
  return true;
});
