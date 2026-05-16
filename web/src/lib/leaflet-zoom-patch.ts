export function patchLeafletZoom() {
  if (typeof window === "undefined") return;
  const fix = () => {
    document.querySelectorAll('.leaflet-control-zoom a[href="#"]').forEach(a => {
      a.setAttribute("href", "javascript:void(0)");
    });
  };
  const observer = new MutationObserver(fix);
  observer.observe(document.body, { childList: true, subtree: true });
  fix();
}
