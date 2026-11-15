/**
 * pages/FaqPage.jsx — public FAQ page (/faq).
 * Content lives in src/data/faq.mjs so this page and the build-time
 * SEO snapshot (scripts/seo-prerender.mjs) can never drift.
 * Questions are the ones people actually search (AlsoAsked,
 * "AI app development", 2026-11-12). Prices are founder-approved.
 */
import React, { useEffect } from "react";
import { Link } from "react-router-dom";
import { FAQ } from "../data/faq";
import MarketingNav from "../components/marketing/MarketingNav";

export default function FaqPage() {
  useEffect(() => {
    const prevTitle = document.title;
    document.title = FAQ.title;
    const graph = [
      {
        "@type": "FAQPage",
        mainEntity: FAQ.items.map(({ q, a }) => ({
          "@type": "Question",
          name: q,
          acceptedAnswer: { "@type": "Answer", text: a },
        })),
      },
      {
        "@type": "BreadcrumbList",
        itemListElement: [
          { "@type": "ListItem", position: 1, name: "AUREM", item: "https://auremcto.com/" },
          { "@type": "ListItem", position: 2, name: "FAQ", item: "https://auremcto.com/faq" },
        ],
      },
    ];
    const ld = document.createElement("script");
    ld.type = "application/ld+json";
    ld.id = "ld-faq";
    ld.text = JSON.stringify({ "@context": "https://schema.org", "@graph": graph });
    document.head.appendChild(ld);
    return () => {
      document.title = prevTitle;
      document.getElementById("ld-faq")?.remove();
    };
  }, []);

  return (
    <div data-testid="faq-page">
      <MarketingNav />
      <div style={{
        maxWidth: 820, margin: "0 auto", padding: "40px 24px 80px",
        color: "var(--text, #e8e3d3)",
        fontFamily: "Inter, system-ui, -apple-system, sans-serif",
        lineHeight: 1.65,
      }}>
      <h1 data-testid="faq-title" style={{
        fontSize: 32, fontWeight: 500, letterSpacing: "-0.02em",
        margin: "16px 0 12px",
      }}>{FAQ.heading}</h1>
      <p data-testid="faq-intro" style={{
        fontSize: 14, color: "var(--text-dim, #a39d8a)", margin: "0 0 24px",
      }}>{FAQ.intro}</p>

      {FAQ.items.map((item, i) => (
        <details key={i} data-testid={`faq-item-${i}`} style={{ marginBottom: 12 }}>
          <summary style={{
            cursor: "pointer", fontSize: 15, fontWeight: 600,
            color: "var(--text, #e8e3d3)",
          }}>{item.q}</summary>
          <p style={{
            fontSize: 14, color: "var(--text-dim, #a39d8a)", margin: "8px 0 0",
          }}>{item.a}</p>
        </details>
      ))}

      <footer style={{
        marginTop: 60, paddingTop: 20,
        borderTop: "1px solid var(--border, rgba(255,200,120,0.16))",
        fontSize: 11, color: "var(--text-faint)",
      }}>
        <Link to="/pricing" style={{ color: "inherit", marginRight: 16 }}>Pricing</Link>
        <Link to="/about" style={{ color: "inherit", marginRight: 16 }}>About</Link>
        <Link to="/contact" style={{ color: "inherit" }}>Contact</Link>
      </footer>
      </div>
    </div>
  );
}
