import { useEffect, useState } from "react";
import { useAuth } from "../../store/auth";
export function Assembly() {
  const [visible, setVisible] = useState(
    () =>
      useAuth.getState().assemblyPending &&
      !window.matchMedia("(prefers-reduced-motion: reduce)").matches &&
      !(navigator.hardwareConcurrency > 0 && navigator.hardwareConcurrency <= 2),
  );
  useEffect(() => {
    useAuth.setState({ assemblyPending: false });
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const finish = () => setVisible(false);
    if (media.matches || (navigator.hardwareConcurrency > 0 && navigator.hardwareConcurrency <= 2))
      finish();
    const change = () => {
      if (media.matches) finish();
    };
    media.addEventListener("change", change);
    const timer = setTimeout(finish, 800);
    return () => {
      clearTimeout(timer);
      media.removeEventListener("change", change);
    };
  }, []);
  return visible ? (
    <div className="hp-assembly" aria-hidden="true">
      <i />
      <i />
      <i />
      <i />
    </div>
  ) : null;
}
