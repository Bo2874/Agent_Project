/* Shared authenticated shell. Chat owns its own conversation controls. */
function initWorkspaceShell(root) {
    if (!root || root.dataset.workspaceInitialized) return;
    root.dataset.workspaceInitialized = "true";
    const toggles = root.querySelectorAll("[data-sidebar-toggle]");
    const shade = root.querySelector("[data-sidebar-dismiss]");
    const mobile = window.matchMedia("(max-width: 760px)");
    let expanded = !mobile.matches;
    function render() {
        root.classList.toggle("sidebar-open", expanded);
        root.classList.toggle("sidebar-collapsed", !expanded);
        toggles.forEach(button => button.setAttribute("aria-expanded", String(expanded)));
        const sidebar = root.querySelector("#workspace-sidebar");
        sidebar.inert = !expanded;
        shade.hidden = !(mobile.matches && expanded);
    }
    toggles.forEach(button => button.addEventListener("click", () => {
        expanded = !expanded;
        render();
    }));
    shade.addEventListener("click", () => { expanded = false; render(); });
    root.addEventListener("keydown", event => {
        if (event.key === "Escape" && mobile.matches && expanded) {
            expanded = false;
            render();
            root.querySelector(".workspace-reopen").focus();
        }
    });
    mobile.addEventListener("change", () => { expanded = !mobile.matches; render(); });
    render();
}
document.querySelectorAll("[data-workspace-shell]").forEach(initWorkspaceShell);
