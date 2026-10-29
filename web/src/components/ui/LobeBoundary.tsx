import { ConfigProvider } from "@lobehub/ui";
import { motion, MotionConfig } from "motion/react";
import type { ReactNode } from "react";
import { useMotionAllowed } from "../motion/preference";

/** Keep popup hosts inside their owning native Surface, including nested dialogs. */
export function LobeBoundary({ children }: { children: ReactNode }) {
  const allowed = useMotionAllowed();
  return (
    <MotionConfig reducedMotion={allowed ? "user" : "always"}>
      <ConfigProvider enableCustomFonts={false} motion={motion} locale="zh-CN">
        {children}
      </ConfigProvider>
    </MotionConfig>
  );
}
