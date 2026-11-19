// AAO 2026-11-19: machine-readable JSON-LD for Aurem Loop (machine-readable-blocks.md).
// The Ora Organization + SoftwareApplication block is served statically in frontend/index.html.
export const LOOP_JSON_LD = {
  "@context": "https://schema.org",
  "@type": "SoftwareApplication",
  "name": "Aurem Loop",
  "applicationCategory": "DeveloperApplication",
  "operatingSystem": "Windows",
  "url": "https://auremcto.com/harness",
  "downloadUrl": "https://auremcto.com/harness",
  "softwareVersion": "0.8.0",
  "datePublished": "2026-11-16",
  "description": "Your code. Your PC. Your model. A free local coding harness that runs entirely on your own PC. Ships qwen2.5-coder:1.5b.",
  "provider": {
    "@type": "Organization",
    "name": "Ora by Aurem",
    "legalName": "Polaris Built Inc.",
    "url": "https://auremcto.com"
  },
  "offers": {
    "@type": "Offer",
    "price": "0",
    "priceCurrency": "USD",
    "availability": "https://schema.org/InStock"
  }
}
;
