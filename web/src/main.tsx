import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Theme } from "@radix-ui/themes";
import "@radix-ui/themes/styles.css";
import "./styles.css";
import { App } from "./App";

const rootElement = document.getElementById("root");
if (!rootElement) {
  throw new Error("root element not found");
}

createRoot(rootElement).render(
  <StrictMode>
    <Theme appearance="light" accentColor="blue" grayColor="slate" panelBackground="solid">
      <App />
    </Theme>
  </StrictMode>,
);
