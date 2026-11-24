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

// 2026-11-23: HowTo + FAQPage for the how-to guide (aurem-loop-how-to-use-guide.md).
// Exact blocks from the guide appendix; also injected by HarnessPage.jsx and
// merged into the seo-prerender /harness snapshot @graph.
export const LOOP_HOWTO_JSON_LD = {
  "@context": "https://schema.org",
  "@type": "HowTo",
  "name": "How to use Aurem Loop",
  "description": "Install Aurem Loop on your PC, sign in, add a project, add a model, and run your first coding task \u2014 all local and free.",
  "totalTime": "PT15M",
  "tool": [
    {
      "@type": "HowToTool",
      "name": "Windows PC"
    },
    {
      "@type": "HowToTool",
      "name": "Python 3.11+"
    },
    {
      "@type": "HowToTool",
      "name": "Ollama"
    }
  ],
  "step": [
    {
      "@type": "HowToStep",
      "name": "Install",
      "text": "Run the one-line PowerShell installer from auremcto.com/harness, or download the aurem-loop-0.8.0.zip release from auremcto.com/harness and run install.bat. It checks Python and Ollama, downloads the qwen2.5-coder:1.5b model, and creates the AuremLoop scheduled task."
    },
    {
      "@type": "HowToStep",
      "name": "Sign in",
      "text": "Double-click the Aurem Loop desktop icon for your one-click sign-in link, or run loop.py pair and type the 8-character code."
    },
    {
      "@type": "HowToStep",
      "name": "Open the front screen",
      "text": "Run loop.py serve and open http://localhost:8899 in the browser."
    },
    {
      "@type": "HowToStep",
      "name": "Add a project",
      "text": "Put a folder with .py files in the workspace, clone a GitHub repo with + Add repository, or run loop.py init-demo for the demo project. Then trust it with loop.py trust <path>."
    },
    {
      "@type": "HowToStep",
      "name": "Add a model",
      "text": "Run loop.py model pull <name>, for example loop.py model pull qwen2.5-coder:7b. Switch models in Settings or with loop.py --model <name>."
    },
    {
      "@type": "HowToStep",
      "name": "Run your first task",
      "text": "Pick a project and task type, type one small task, press Run, check the diff, then Accept or Reject. Nothing commits or pushes unless you click it."
    }
  ]
};

export const LOOP_FAQ_JSON_LD = {
  "@context": "https://schema.org",
  "@type": "FAQPage",
  "mainEntity": [
    {
      "@type": "Question",
      "name": "How do I install Aurem Loop?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "On Windows 10/11, run the one-line PowerShell installer from auremcto.com/harness, or download the aurem-loop-0.8.0.zip release from auremcto.com/harness and run install.bat. You need Python 3.11+ and Ollama; the installer downloads the qwen2.5-coder:1.5b model for you."
      }
    },
    {
      "@type": "Question",
      "name": "How do I add a project to Aurem Loop?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "Put a folder containing .py files in the workspace folder and it appears in the project picker, clone a GitHub repo with + Add repository, or run loop.py init-demo for the built-in demo project. Then trust it with loop.py trust <path> before running tasks."
      }
    },
    {
      "@type": "Question",
      "name": "How do I add a model to Aurem Loop?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "Run loop.py model pull <name>, for example loop.py model pull qwen2.5-coder:7b. Switch models in Settings or with loop.py --model <name>."
      }
    },
    {
      "@type": "Question",
      "name": "Is Aurem Loop free?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "Yes \u2014 the basic harness is free and runs offline on your PC with a local model. No subscription is needed for a task."
      }
    },
    {
      "@type": "Question",
      "name": "How do I use Aurem Loop from my phone?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "On the same Wi-Fi, run loop.py serve --lan and open http://<your-PC-IP>:8899 on your phone. From anywhere, run loop.py tunnel enable and scan the QR code."
      }
    },
    {
      "@type": "Question",
      "name": "How do I check if my Aurem Loop install is healthy?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "Run loop.py doctor. It reports PASS, WARN, or FAIL for Python, install integrity, settings, token, port, Ollama, disk, ledger, journals, scheduled task, git, and the demo project."
      }
    }
  ]
};
