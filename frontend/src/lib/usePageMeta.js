/**
 * usePageMeta — tiny hook to set <title> + meta description per route.
 * Avoids pulling in react-helmet-async just for two auth pages.
 *
 * Restores the default landing meta on unmount so users navigating back
 * to / don't see stale tab titles.
 */
import { useEffect } from "react";
import { pageTitle } from "./siteConfig";
import { defaultPageMetaDescription } from "./billingCopy";

// 2026-09-17 — Bug 7 fix: this used to hardcode its OWN third
// variant of the title ("...Your Autonomous CTO. Ships code to
// GitHub in minutes.") instead of the shared one — see siteConfig.js.
const DEFAULT_TITLE = pageTitle();
const DEFAULT_DESC = defaultPageMetaDescription();

function setMeta(name, content, attr = "name") {
  let tag = document.querySelector(`meta[${attr}="${name}"]`);
  if (!tag) {
    tag = document.createElement("meta");
    tag.setAttribute(attr, name);
    document.head.appendChild(tag);
  }
  tag.setAttribute("content", content);
}

function setCanonical(href) {
  let tag = document.querySelector('link[rel="canonical"]');
  if (!tag) {
    tag = document.createElement("link");
    tag.setAttribute("rel", "canonical");
    document.head.appendChild(tag);
  }
  tag.setAttribute("href", href);
}

function setMarkdownAlternate(href) {
  let tag = null;
  const links = document.querySelectorAll("link[rel=alternate]");
  for (const l of links) {
    if (l.getAttribute("type") === "text/markdown") {
      tag = l;
      break;
    }
  }
  if (tag === null) {
    tag = document.createElement("link");
    tag.setAttribute("rel", "alternate");
    tag.setAttribute("type", "text/markdown");
    document.head.appendChild(tag);
  }
  tag.setAttribute("href", href);
}
function markdownHrefFor(canonical) {
  try {
    let path = new URL(canonical).pathname;
    if (path === "/") return "/index.md";
    if (path.endsWith("/")) path = path.slice(0, -1);
    return path + ".md";
  } catch {
    return "/index.md";
  }
}
export default function usePageMeta({ title, description, canonical }) {
  useEffect(() => {
    if (title) document.title = title;
    if (description) {
      setMeta("description", description);
      setMeta("og:title", title || DEFAULT_TITLE, "property");
      setMeta("og:description", description, "property");
      setMeta("twitter:title", title || DEFAULT_TITLE);
      setMeta("twitter:description", description);
    }
    if (canonical) {
      setCanonical(canonical);
      setMarkdownAlternate(markdownHrefFor(canonical));
    }
    return () => {
      document.title = DEFAULT_TITLE;
      setMeta("description", DEFAULT_DESC);
      setMeta("og:title", DEFAULT_TITLE, "property");
      setMeta("og:description", DEFAULT_DESC, "property");
      setMeta("twitter:title", DEFAULT_TITLE);
      setMeta("twitter:description", DEFAULT_DESC);
      setCanonical(window.location.origin + "/");
      setMarkdownAlternate("/index.md");
    };
  }, [title, description, canonical]);
}


