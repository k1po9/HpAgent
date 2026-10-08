/** Native semantics decide eligibility; only modal boundaries need our wrap. */
export function tabOrder(root: HTMLElement) {
  const candidates = Array.from(
    root.querySelectorAll<HTMLElement>(
      "a[href], area[href], button, input, select, textarea, summary, iframe, object, embed, audio[controls], video[controls], [contenteditable], [tabindex]",
    ),
  ).filter((element) => {
    const editable =
      element.isContentEditable &&
      !element.hasAttribute("tabindex") &&
      !element.parentElement?.isContentEditable;
    if (
      (!editable && element.tabIndex < 0) ||
      element.matches(":disabled") ||
      element.closest("[hidden], [inert]")
    )
      return false;
    if (
      element.tagName === "SUMMARY" &&
      (element.parentElement?.tagName !== "DETAILS" ||
        element.parentElement.querySelector("summary") !== element)
    )
      return false;
    for (let ancestor = element.parentElement; ancestor; ancestor = ancestor.parentElement) {
      if (
        ancestor.tagName === "DETAILS" &&
        !ancestor.hasAttribute("open") &&
        !ancestor.querySelector("summary")?.contains(element)
      )
        return false;
    }
    const style = getComputedStyle(element);
    return (
      element.getClientRects().length > 0 &&
      style.visibility !== "hidden" &&
      style.visibility !== "collapse"
    );
  });
  return candidates
    .filter((element) => {
      if (!(element instanceof HTMLInputElement) || element.type !== "radio" || !element.name)
        return true;
      const group = candidates.filter(
        (other) =>
          other instanceof HTMLInputElement &&
          other.type === "radio" &&
          other.name === element.name &&
          other.form === element.form,
      ) as HTMLInputElement[];
      return element === (group.find((radio) => radio.checked) ?? group[0]);
    })
    .sort(
      (a, b) => (a.tabIndex > 0 ? a.tabIndex : Infinity) - (b.tabIndex > 0 ? b.tabIndex : Infinity),
    );
}
