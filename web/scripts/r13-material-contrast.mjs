/* global document, getComputedStyle, Node */
// Solid rendered CSS contrast audit; excludes disabled text and user-authored iframes.
export async function auditContrast(page) {
  return page.evaluate(() => {
    const rgba = (s) => {
      const n = s.match(/[\d.]+/g)?.map(Number);
      return n?.length >= 3 ? [n[0], n[1], n[2], n[3] ?? 1] : null;
    };
    const blend = (fg, bg) => fg.slice(0, 3).map((v, i) => v * fg[3] + bg[i] * (1 - fg[3]));
    const luminance = (color) =>
      color.slice(0, 3).reduce((v, c, i) => {
        const n = c / 255;
        return (
          v +
          (n <= 0.04045 ? n / 12.92 : ((n + 0.055) / 1.055) ** 2.4) * [0.2126, 0.7152, 0.0722][i]
        );
      }, 0);
    const contrast = (a, b) => {
      const [hi, lo] = [luminance(a), luminance(b)].sort((a, b) => b - a);
      return (hi + 0.05) / (lo + 0.05);
    };
    const background = (el) => {
      const chain = [];
      for (let p = el; p; p = p.parentElement) chain.unshift(p);
      return chain.reduce(
        (bg, p) => blend(rgba(getComputedStyle(p).backgroundColor) ?? [0, 0, 0, 0], bg),
        [255, 255, 255],
      );
    };
    const violations = [];
    const legacyBlue = [];
    let checked = 0;
    for (const el of document.querySelectorAll("body *")) {
      if (
        !Array.from(el.childNodes).some(
          (n) => n.nodeType === Node.TEXT_NODE && n.textContent.trim(),
        )
      )
        continue;
      if (el.closest('svg,style,script,[inert],:disabled,[aria-disabled="true"]')) continue;
      const rect = el.getBoundingClientRect();
      const style = getComputedStyle(el);
      if (
        !rect.width ||
        !rect.height ||
        (rect.width <= 1 &&
          rect.height <= 1 &&
          (style.clip !== "auto" || style.clipPath !== "none")) ||
        rect.bottom <= 0 ||
        rect.top >= document.documentElement.clientHeight ||
        style.visibility !== "visible"
      )
        continue;
      const bg = background(el);
      const fg = rgba(style.color);
      if (!fg) continue;
      if (fg[2] > 150 && fg[2] > fg[0] * 1.2 && fg[2] > fg[1] * 1.2)
        legacyBlue.push({ text: el.textContent.trim().slice(0, 50), color: style.color });
      const size = parseFloat(style.fontSize);
      const threshold =
        size >= 24 || (size >= 18.66 && parseInt(style.fontWeight) >= 700) ? 3 : 4.5;
      const ratio = contrast(blend(fg, bg), bg);
      checked++;
      if (ratio + 0.01 < threshold)
        violations.push({
          text: el.textContent.trim().slice(0, 70),
          selector: el.tagName + "." + el.className,
          color: style.color,
          background: bg,
          ratio,
          threshold,
        });
    }
    const tokens = {};
    const style = getComputedStyle(
      document.querySelector(".radix-themes") ?? document.documentElement,
    );
    for (const name of [
      "--hp-primary",
      "--hp-accent",
      "--hp-muted",
      "--hp-control-border",
      "--hp-motion-star-neutral",
      "--hp-motion-star-core",
    ])
      tokens[name] = style.getPropertyValue(name).trim();
    const controls = [];
    for (const selector of [
      ".hp-conversation-filter",
      ".hp-composer",
      ".hp-workspace-search input",
      ".hp-auth-card input",
    ]) {
      const el = document.querySelector(selector);
      if (!el || !el.getBoundingClientRect().width) continue;
      const s = getComputedStyle(el);
      const bg = background(el);
      const border = rgba(s.borderColor);
      if (border && parseFloat(s.borderWidth))
        controls.push({ selector, ratio: contrast(blend(border, bg), bg), color: s.borderColor });
      else controls.push({ selector, shadow: s.boxShadow });
    }
    return { checked, violations, legacyBlue, tokens, controls };
  });
}
