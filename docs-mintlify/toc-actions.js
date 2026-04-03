// Add custom actions to "On this page" table of contents
(function() {
  // Convert HTML to Markdown
  function htmlToMarkdown(element) {
    let markdown = '';

    // Get page title
    const title = document.querySelector('h1')?.textContent || document.title;
    markdown += `# ${title}\n\n`;

    // Process all child nodes
    function processNode(node) {
      if (node.nodeType === Node.TEXT_NODE) {
        const text = node.textContent.trim();
        if (text) return text;
        return '';
      }

      if (node.nodeType !== Node.ELEMENT_NODE) return '';

      const tag = node.tagName.toLowerCase();
      let result = '';

      // Headings
      if (tag.match(/^h[2-6]$/)) {
        const level = parseInt(tag[1]);
        const text = node.textContent.trim();
        result = '\n' + '#'.repeat(level) + ' ' + text + '\n\n';
      }
      // Paragraphs
      else if (tag === 'p') {
        result = processChildren(node) + '\n\n';
      }
      // Links
      else if (tag === 'a') {
        const href = node.getAttribute('href') || '';
        const text = node.textContent.trim();
        result = `[${text}](${href})`;
      }
      // Strong/Bold
      else if (tag === 'strong' || tag === 'b') {
        result = `**${processChildren(node)}**`;
      }
      // Emphasis/Italic
      else if (tag === 'em' || tag === 'i') {
        result = `*${processChildren(node)}*`;
      }
      // Code inline
      else if (tag === 'code' && node.parentElement.tagName !== 'PRE') {
        result = `\`${node.textContent}\``;
      }
      // Code blocks
      else if (tag === 'pre') {
        const code = node.querySelector('code');
        const language = code?.className.match(/language-(\w+)/)?.[1] || '';
        const codeText = code?.textContent || node.textContent;
        result = '\n```' + language + '\n' + codeText.trim() + '\n```\n\n';
      }
      // Lists
      else if (tag === 'ul') {
        result = '\n' + processListItems(node, false) + '\n';
      }
      else if (tag === 'ol') {
        result = '\n' + processListItems(node, true) + '\n';
      }
      else if (tag === 'li') {
        // Handled by processListItems
        result = processChildren(node);
      }
      // Blockquotes
      else if (tag === 'blockquote') {
        const lines = processChildren(node).split('\n');
        result = lines.map(line => line ? '> ' + line : '>').join('\n') + '\n\n';
      }
      // Images
      else if (tag === 'img') {
        const src = node.getAttribute('src') || '';
        const alt = node.getAttribute('alt') || '';
        result = `![${alt}](${src})`;
      }
      // Horizontal rule
      else if (tag === 'hr') {
        result = '\n---\n\n';
      }
      // Tables
      else if (tag === 'table') {
        result = processTable(node);
      }
      // Line breaks
      else if (tag === 'br') {
        result = '\n';
      }
      // Default: process children
      else {
        result = processChildren(node);
      }

      return result;
    }

    function processChildren(node) {
      let result = '';
      for (const child of node.childNodes) {
        result += processNode(child);
      }
      return result;
    }

    function processListItems(listNode, ordered) {
      let result = '';
      let index = 1;
      for (const li of listNode.children) {
        if (li.tagName === 'LI') {
          const prefix = ordered ? `${index}. ` : '- ';
          const content = processChildren(li).trim();
          result += prefix + content + '\n';
          index++;
        }
      }
      return result;
    }

    function processTable(tableNode) {
      let result = '\n';
      const rows = tableNode.querySelectorAll('tr');

      rows.forEach((row, rowIndex) => {
        const cells = row.querySelectorAll('th, td');
        const cellContents = Array.from(cells).map(cell => cell.textContent.trim());
        result += '| ' + cellContents.join(' | ') + ' |\n';

        // Add separator after header row
        if (rowIndex === 0 && row.querySelector('th')) {
          result += '| ' + cellContents.map(() => '---').join(' | ') + ' |\n';
        }
      });

      return result + '\n';
    }

    // Process the content
    markdown += processChildren(element);

    // Clean up extra newlines
    markdown = markdown.replace(/\n{3,}/g, '\n\n').trim();

    return markdown;
  }

  function addTocActions() {
    // Find the table of contents container
    const tocSelectors = [
      '#table-of-contents-content',
      '[class*="table-of-contents"]',
      'nav[aria-label*="Table of contents"]',
      '[data-testid="table-of-contents"]'
    ];
    
    let tocContainer = null;
    for (const selector of tocSelectors) {
      tocContainer = document.querySelector(selector);
      if (tocContainer) break;
    }
    
    if (!tocContainer) return;
    
    // Check if we already added the actions
    if (tocContainer.querySelector('.toc-custom-actions')) return;
    
    // Create the actions container
    const actionsDiv = document.createElement('div');
    actionsDiv.className = 'toc-custom-actions';
    
    // Create "Copy page as markdown" action
    const copyAction = document.createElement('div');
    copyAction.className = 'toc-custom-action';
    copyAction.style.display = 'flex';
    copyAction.style.alignItems = 'center';
    copyAction.style.gap = '0.5rem';

    // Add markdown icon (SVG)
    const markdownIcon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    markdownIcon.setAttribute('width', '16');
    markdownIcon.setAttribute('height', '16');
    markdownIcon.setAttribute('viewBox', '0 0 32 32');
    markdownIcon.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
    markdownIcon.className = 'toc-action-icon markdown-icon';
    markdownIcon.style.flexShrink = '0';

    const markdownPath = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    markdownPath.setAttribute('fill', '#444444');
    markdownPath.setAttribute('d', 'M25.674 9.221h-19.348c-0.899 0-1.63 0.731-1.63 1.63v10.869c0 0.899 0.731 1.63 1.63 1.63h19.348c0.899 0 1.63-0.731 1.63-1.63v-10.869c0-0.899-0.731-1.63-1.63-1.63zM17.413 20.522l-2.826 0.003v-4.239l-2.12 2.717-2.12-2.717v4.239h-2.826v-8.478h2.826l2.12 2.826 2.12-2.826 2.826-0.003v8.478zM21.632 21.229l-3.512-4.943h2.119v-4.239h2.826v4.239h2.119l-3.553 4.943z');

    markdownIcon.appendChild(markdownPath);

    const copyText = document.createElement('span');
    copyText.textContent = 'Copy page as markdown';

    copyAction.appendChild(markdownIcon);
    copyAction.appendChild(copyText);

    copyAction.onclick = async function() {
      try {
        // Get the main content container
        const contentSelectors = [
          '#content',
          '.mdx-content',
          'article',
          'main',
          '[role="main"]'
        ];

        let contentElement = null;
        for (const selector of contentSelectors) {
          contentElement = document.querySelector(selector);
          if (contentElement) break;
        }

        if (!contentElement) {
          alert('Could not find page content to copy');
          return;
        }

        // Convert HTML to markdown
        let markdown = htmlToMarkdown(contentElement);

        // Copy to clipboard
        await navigator.clipboard.writeText(markdown);

        // Show feedback
        const originalText = copyText.textContent;
        copyText.textContent = '✓ Copied!';
        setTimeout(() => {
          copyText.textContent = originalText;
        }, 2000);
      } catch (error) {
        console.error('Failed to copy:', error);
        alert('Failed to copy page content');
      }
    };
    
    // Create "Open in ChatGPT" action
    const chatgptAction = document.createElement('a');
    chatgptAction.className = 'toc-custom-action';
    chatgptAction.style.textDecoration = 'none';
    chatgptAction.style.display = 'flex';
    chatgptAction.style.alignItems = 'center';
    chatgptAction.style.gap = '0.5rem';

    // Add ChatGPT icon (SVG)
    const chatgptIcon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    chatgptIcon.setAttribute('width', '16');
    chatgptIcon.setAttribute('height', '16');
    chatgptIcon.setAttribute('viewBox', '0 0 320 320');
    chatgptIcon.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
    chatgptIcon.className = 'toc-action-icon chatgpt-icon';
    chatgptIcon.style.flexShrink = '0';

    const chatgptPath = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    chatgptPath.setAttribute('fill', '#444444');
    chatgptPath.setAttribute('d', 'm297.06 130.97c7.26-21.79 4.76-45.66-6.85-65.48-17.46-30.4-52.56-46.04-86.84-38.68-15.25-17.18-37.16-26.95-60.13-26.81-35.04-.08-66.13 22.48-76.91 55.82-22.51 4.61-41.94 18.7-53.31 38.67-17.59 30.32-13.58 68.54 9.92 94.54-7.26 21.79-4.76 45.66 6.85 65.48 17.46 30.4 52.56 46.04 86.84 38.68 15.24 17.18 37.16 26.95 60.13 26.8 35.06.09 66.16-22.49 76.94-55.86 22.51-4.61 41.94-18.7 53.31-38.67 17.57-30.32 13.55-68.51-9.94-94.51zm-120.28 168.11c-14.03.02-27.62-4.89-38.39-13.88.49-.26 1.34-.73 1.89-1.07l63.72-36.8c3.26-1.85 5.26-5.32 5.24-9.07v-89.83l26.93 15.55c.29.14.48.42.52.74v74.39c-.04 33.08-26.83 59.9-59.91 59.97zm-128.84-55.03c-7.03-12.14-9.56-26.37-7.15-40.18.47.28 1.3.79 1.89 1.13l63.72 36.8c3.23 1.89 7.23 1.89 10.47 0l77.79-44.92v31.1c.02.32-.13.63-.38.83l-64.41 37.19c-28.69 16.52-65.33 6.7-81.92-21.95zm-16.77-139.09c7-12.16 18.05-21.46 31.21-26.29 0 .55-.03 1.52-.03 2.2v73.61c-.02 3.74 1.98 7.21 5.23 9.06l77.79 44.91-26.93 15.55c-.27.18-.61.21-.91.08l-64.42-37.22c-28.63-16.58-38.45-53.21-21.95-81.89zm221.26 51.49-77.79-44.92 26.93-15.54c.27-.18.61-.21.91-.08l64.42 37.19c28.68 16.57 38.51 53.26 21.94 81.94-7.01 12.14-18.05 21.44-31.2 26.28v-75.81c.03-3.74-1.96-7.2-5.2-9.06zm26.8-40.34c-.47-.29-1.3-.79-1.89-1.13l-63.72-36.8c-3.23-1.89-7.23-1.89-10.47 0l-77.79 44.92v-31.1c-.02-.32.13-.63.38-.83l64.41-37.16c28.69-16.55 65.37-6.7 81.91 22 6.99 12.12 9.52 26.31 7.15 40.1zm-168.51 55.43-26.94-15.55c-.29-.14-.48-.42-.52-.74v-74.39c.02-33.12 26.89-59.96 60.01-59.94 14.01 0 27.57 4.92 38.34 13.88-.49.26-1.33.73-1.89 1.07l-63.72 36.8c-3.26 1.85-5.26 5.31-5.24 9.06l-.04 89.79zm14.63-31.54 34.65-20.01 34.65 20v40.01l-34.65 20-34.65-20z');

    chatgptIcon.appendChild(chatgptPath);

    const chatgptText = document.createElement('span');
    chatgptText.textContent = 'Open in ChatGPT';

    chatgptAction.appendChild(chatgptIcon);
    chatgptAction.appendChild(chatgptText);

    // Set target and rel attributes
    chatgptAction.target = '_blank';
    chatgptAction.rel = 'noopener noreferrer';

    // Update the href dynamically on click to get the current page URL
    chatgptAction.onclick = function(e) {
      // Get the current page URL - replace localhost with production URL
      let currentPageUrl = window.location.href;
      if (currentPageUrl.includes('localhost:3000')) {
        currentPageUrl = currentPageUrl.replace('http://localhost:3000', 'https://www.tracer.cloud/docs');
      }

      // Create the prompt with the page URL
      const prompt = `Read ${currentPageUrl}`;

      // Encode the prompt for URL
      const encodedPrompt = encodeURIComponent(prompt);

      // Update the href with the current page URL
      chatgptAction.href = `https://chat.openai.com/?q=${encodedPrompt}`;
    };
    
    // Append actions to container
    actionsDiv.appendChild(copyAction);
    actionsDiv.appendChild(chatgptAction);
    
    // Append to TOC
    tocContainer.appendChild(actionsDiv);
  }
  
  // Run when DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', addTocActions);
  } else {
    addTocActions();
  }

  // Also run after a short delay to catch dynamically loaded content
  setTimeout(addTocActions, 500);
  setTimeout(addTocActions, 1000);
  setTimeout(addTocActions, 2000);

  // Use MutationObserver to watch for TOC being added to the DOM
  const observer = new MutationObserver(function(mutations) {
    addTocActions();
  });

  // Start observing the document body for changes
  observer.observe(document.body, {
    childList: true,
    subtree: true
  });

  // Also listen for route changes (for single-page apps)
  window.addEventListener('popstate', function() {
    setTimeout(addTocActions, 100);
    setTimeout(addTocActions, 500);
  });
})();

