import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./styles/index.css";

// The pages come next; for now the page only shows that the build and the styles work.
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <p className="p-8 text-page font-semibold text-ink">Ambulance Dispatch</p>
  </StrictMode>,
);
