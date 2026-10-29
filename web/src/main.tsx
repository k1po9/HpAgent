import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Theme } from "@radix-ui/themes";
import "@radix-ui/themes/styles.css";
import "./styles.css";
import "@lobehub/ui/theme.css";
import "@lobehub/ui/style.css";
import "./styles/task-lobe.css";
import "./styles/workspace-lobe.css";
import { LobeBoundary } from "./components/ui/LobeBoundary";
import { App } from "./App";

const rootElement = document.getElementById("root");
if (!rootElement) {
  throw new Error("root element not found");
}

createRoot(rootElement).render(
  <StrictMode>
    <Theme appearance="light" accentColor="tomato" grayColor="slate" panelBackground="solid">
      <LobeBoundary>
        <App />
      </LobeBoundary>
    </Theme>
  </StrictMode>,
);
