import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

// Self-hosted, no Google Fonts link: the app is deployed behind a strict setup
// and a blocked stylesheet would silently take the whole type system with it.
// Only the axes and weights the design actually uses are imported.
import "@fontsource-variable/bricolage-grotesque/wght.css";
import "@fontsource/ibm-plex-sans/latin-400.css";
import "@fontsource/ibm-plex-sans/latin-500.css";
import "@fontsource/ibm-plex-sans/latin-600.css";
import "@fontsource/ibm-plex-mono/latin-400.css";
import "@fontsource/ibm-plex-mono/latin-500.css";

import App from "./App";
import "./index.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
