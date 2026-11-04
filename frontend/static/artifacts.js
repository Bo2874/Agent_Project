(() => {
  const root = document.querySelector("[data-artifacts-layout]");
  if (!root || !window.createArtifactPanel || !window.ArtifactWorkspace) return;
  async function requestJson(url, options = {}) {
    const response = await fetch(url, { credentials: "same-origin", cache: "no-store", ...options });
    if (response.status === 401) { location.assign("/login"); throw new Error("Phiên đăng nhập đã hết hạn."); }
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Không thể mở file.");
    return data;
  }
  const panel = window.createArtifactPanel(root, { requestJson });
  window.ArtifactWorkspace.onOpen = (id, context) => panel.open(id, context);
  window.dispatchEvent(new CustomEvent("artifact-preview-available"));
})();
