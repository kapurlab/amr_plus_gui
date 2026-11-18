import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import { PageAskHost } from "./pageAsk.jsx";

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <App />
    <PageAskHost />
  </StrictMode>
);
