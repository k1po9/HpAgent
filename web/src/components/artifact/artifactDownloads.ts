const urls = new Map<string, ReturnType<typeof setTimeout>>();
export const artifactFilename = (title: string, version: number) =>
  `${
    Array.from(title, (char) => (char.charCodeAt(0) < 32 ? "_" : char))
      .join("")
      .replace(/[<>:"/\\|?*]/g, "_")
      .trim()
      .replace(/[. ]+$/, "")
      .slice(0, 120) || "HTML成果"
  }-v${version}.html`;
export function clearArtifactDownloads() {
  urls.forEach((timer, url) => {
    clearTimeout(timer);
    URL.revokeObjectURL(url);
  });
  urls.clear();
}
export function downloadArtifact(html: string, name: string) {
  const url = URL.createObjectURL(new Blob([html], { type: "text/html" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  urls.set(
    url,
    setTimeout(() => {
      URL.revokeObjectURL(url);
      urls.delete(url);
    }, 1000),
  );
}
