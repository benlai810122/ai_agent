/* ── Visual Flow Panel ─────────────────────────────────────────────────────
   Renders a test script (delivered as a `[Flow]` progress event) as a flow
   chart in a right-side docked drawer that can pop out into a floating,
   resizable panel, or combine into the page layout (pushing the main content
   left). `[Node]` / `[Round]` events drive the live "currently running"
   highlight. No external libraries. */
(function () {
    "use strict";

    var LS_KEY = "flowPanelState";
    var panel, toggleBtn, canvasEl, titleEl, roundBadge, currentEl, resizeEl;
    var model = null;
    var nodeEls = {};       // id -> node element
    var nodeSummary = {};   // id -> short label for the "current" readout
    var persisted = loadState();

    function loadState() {
        try { return JSON.parse(localStorage.getItem(LS_KEY)) || {}; }
        catch (e) { return {}; }
    }
    function saveState() {
        try { localStorage.setItem(LS_KEY, JSON.stringify(persisted)); } catch (e) {}
    }

    function esc(s) {
        return String(s == null ? "" : s)
            .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
            .replaceAll('"', "&quot;").replaceAll("'", "&#039;");
    }

    // ── DOM construction ──
    function build() {
        toggleBtn = document.createElement("button");
        toggleBtn.id = "flow-toggle";
        toggleBtn.innerHTML = 'FLOW<span class="flow-toggle-badge" id="flow-toggle-badge" style="display:none">1</span>';
        toggleBtn.addEventListener("click", toggle);
        document.body.appendChild(toggleBtn);

        panel = document.createElement("div");
        panel.id = "flow-panel";
        panel.className = "flow-hidden";
        panel.innerHTML =
            '<div id="flow-resize"></div>' +
            '<div class="flow-header">' +
                '<span class="flow-title" id="flow-title">Test Flow</span>' +
                '<button class="flow-btn" id="flow-popout" title="Pop out / dock">\u2197</button>' +
                '<button class="flow-btn" id="flow-dock" title="Combine with page">\u25E7</button>' +
                '<button class="flow-btn" id="flow-close" title="Hide">\u2715</button>' +
            '</div>' +
            '<div class="flow-status">' +
                '<span class="flow-round-badge" id="flow-round" style="display:none">Round 1/1</span>' +
                '<span class="flow-current" id="flow-current"></span>' +
            '</div>' +
            '<div class="flow-canvas" id="flow-canvas">' +
                '<div class="flow-empty">No test flow yet.<br>Ask for a test plan and the flow chart will appear here.</div>' +
            '</div>';
        document.body.appendChild(panel);

        canvasEl = document.getElementById("flow-canvas");
        titleEl = document.getElementById("flow-title");
        roundBadge = document.getElementById("flow-round");
        currentEl = document.getElementById("flow-current");
        resizeEl = document.getElementById("flow-resize");

        document.getElementById("flow-close").addEventListener("click", close);
        document.getElementById("flow-popout").addEventListener("click", togglePopout);
        document.getElementById("flow-dock").addEventListener("click", toggleDock);

        restoreGeometry();
        initResize();
        initDrag();
    }

    // ── Panel visibility / modes ──
    function open() { panel.classList.remove("flow-hidden"); persisted.open = true; saveState(); applyDockLayout(); }
    function close() { panel.classList.add("flow-hidden"); persisted.open = false; saveState(); applyDockLayout(); }
    function toggle() { panel.classList.contains("flow-hidden") ? open() : close(); }

    function togglePopout() {
        var on = !panel.classList.contains("flow-popout");
        panel.classList.toggle("flow-popout", on);
        document.getElementById("flow-popout").classList.toggle("active", on);
        persisted.popout = on;
        if (on) {
            // Floating and combined-with-page are mutually exclusive.
            persisted.docked = false;
            document.getElementById("flow-dock").classList.remove("active");
            applyGeometry();
        } else {
            clearFloatGeometry();
        }
        applyDockLayout();
        saveState();
    }

    // Combine the panel into the page layout: the main content is pushed left so
    // the panel sits beside it instead of overlaying it.
    function toggleDock() {
        var on = !persisted.docked;
        persisted.docked = on;
        document.getElementById("flow-dock").classList.toggle("active", on);
        if (on) {
            if (panel.classList.contains("flow-popout")) togglePopout();  // needs the docked drawer
            open();
        }
        applyDockLayout();
        saveState();
    }

    // Push the page content aside when the panel is docked, combined, and visible.
    function applyDockLayout() {
        var docked = !!persisted.docked
            && !panel.classList.contains("flow-popout")
            && !panel.classList.contains("flow-hidden");
        document.body.classList.toggle("flow-docked", docked);
        if (docked) {
            document.body.style.setProperty("--flow-width", panel.offsetWidth + "px");
        } else {
            document.body.style.removeProperty("--flow-width");
        }
    }

    function restoreGeometry() {
        if (persisted.dockWidth) panel.style.width = persisted.dockWidth + "px";
        if (persisted.popout) {
            panel.classList.add("flow-popout");
            document.getElementById("flow-popout").classList.add("active");
            applyGeometry();
        }
        if (persisted.docked && !persisted.popout) {
            document.getElementById("flow-dock").classList.add("active");
        }
        if (persisted.open) panel.classList.remove("flow-hidden");
        applyDockLayout();
    }
    function applyGeometry() {
        var g = persisted.float || {};
        if (g.left != null) { panel.style.left = g.left + "px"; panel.style.right = "auto"; }
        if (g.top != null) panel.style.top = g.top + "px";
        if (g.width) panel.style.width = g.width + "px";
        if (g.height) panel.style.height = g.height + "px";
    }
    function clearFloatGeometry() {
        panel.style.left = ""; panel.style.top = ""; panel.style.right = "";
        panel.style.height = "";
        panel.style.width = (persisted.dockWidth || 420) + "px";
    }

    // ── Docked left-edge resize ──
    function initResize() {
        var dragging = false;
        resizeEl.addEventListener("mousedown", function (e) {
            if (panel.classList.contains("flow-popout")) return;
            dragging = true; e.preventDefault();
            document.body.style.userSelect = "none";
        });
        window.addEventListener("mousemove", function (e) {
            if (!dragging) return;
            var w = Math.min(window.innerWidth * 0.92, Math.max(300, window.innerWidth - e.clientX));
            panel.style.width = w + "px";
            applyDockLayout();  // keep the page gap in sync while resizing
        });
        window.addEventListener("mouseup", function () {
            if (!dragging) return;
            dragging = false; document.body.style.userSelect = "";
            persisted.dockWidth = parseInt(panel.style.width, 10) || 420;
            saveState();
        });
        // Persist pop-out size changes made via the CSS resize handle.
        if (window.ResizeObserver) {
            new ResizeObserver(function () {
                if (panel.classList.contains("flow-popout")) {
                    persisted.float = persisted.float || {};
                    persisted.float.width = panel.offsetWidth;
                    persisted.float.height = panel.offsetHeight;
                    saveState();
                }
            }).observe(panel);
        }
    }

    // ── Pop-out header drag-to-move ──
    function initDrag() {
        var header = panel.querySelector(".flow-header");
        var dragging = false, ox = 0, oy = 0;
        header.addEventListener("mousedown", function (e) {
            if (!panel.classList.contains("flow-popout")) return;
            if (e.target.classList.contains("flow-btn")) return;
            dragging = true;
            var rect = panel.getBoundingClientRect();
            ox = e.clientX - rect.left; oy = e.clientY - rect.top;
            e.preventDefault(); document.body.style.userSelect = "none";
        });
        window.addEventListener("mousemove", function (e) {
            if (!dragging) return;
            var left = Math.max(0, Math.min(window.innerWidth - 80, e.clientX - ox));
            var top = Math.max(0, Math.min(window.innerHeight - 40, e.clientY - oy));
            panel.style.left = left + "px"; panel.style.right = "auto"; panel.style.top = top + "px";
        });
        window.addEventListener("mouseup", function () {
            if (!dragging) return;
            dragging = false; document.body.style.userSelect = "";
            persisted.float = persisted.float || {};
            persisted.float.left = parseInt(panel.style.left, 10);
            persisted.float.top = parseInt(panel.style.top, 10);
            persisted.float.width = panel.offsetWidth;
            persisted.float.height = panel.offsetHeight;
            saveState();
        });
    }

    // ── Rendering ──
    function renderNodes(nodes, container) {
        (nodes || []).forEach(function (node, idx) {
            if (idx > 0) {
                var conn = document.createElement("div");
                conn.className = "flow-connector";
                conn.textContent = "\u2193";
                container.appendChild(conn);
            }
            var wrap = document.createElement("div");
            var num = node.id.split("-").pop();
            if (node.type === "condition") {
                var el = document.createElement("div");
                el.className = "flow-node flow-condition";
                el.dataset.id = node.id;
                el.innerHTML =
                    '<div class="flow-node-index">' + esc(num) + "</div>" +
                    '<div class="flow-node-state"></div>' +
                    '<div class="flow-cond-label">IF &middot; ' + esc(node.condition || "") + "</div>" +
                    '<div class="flow-node-fn">' + esc(node.function) + "</div>";
                container.appendChild(el);
                nodeEls[node.id] = el;
                nodeSummary[node.id] = "IF " + node.function;

                var branches = document.createElement("div");
                branches.className = "flow-branches";
                branches.appendChild(buildBranch("then", node.then));
                branches.appendChild(buildBranch("else", node.else));
                container.appendChild(branches);
            } else {
                var a = document.createElement("div");
                a.className = "flow-node";
                a.dataset.id = node.id;
                a.innerHTML =
                    '<div class="flow-node-index">' + esc(num) + "</div>" +
                    '<div class="flow-node-state"></div>' +
                    '<div class="flow-node-fn">' + esc(node.function) + "</div>" +
                    (node.summary ? '<div class="flow-node-args">' + esc(node.summary) + "</div>" : "");
                container.appendChild(a);
                nodeEls[node.id] = a;
                nodeSummary[node.id] = node.function;
            }
        });
    }

    function buildBranch(kind, nodes) {
        var b = document.createElement("div");
        b.className = "flow-branch flow-" + kind;
        var label = document.createElement("div");
        label.className = "flow-branch-label";
        label.textContent = kind.toUpperCase();
        b.appendChild(label);
        if (!nodes || !nodes.length) {
            var empty = document.createElement("div");
            empty.className = "flow-branch-empty";
            empty.textContent = "(no steps)";
            b.appendChild(empty);
        } else {
            renderNodes(nodes, b);
        }
        return b;
    }

    function render(newModel) {
        model = newModel;
        nodeEls = {};
        nodeSummary = {};
        canvasEl.innerHTML = "";
        titleEl.textContent = model.task ? ("Flow: " + model.task) : "Test Flow";

        (model.groups || []).forEach(function (group) {
            var g = document.createElement("div");
            g.className = "flow-group";
            g.dataset.group = group.key;
            var t = document.createElement("div");
            t.className = "flow-group-title";
            t.textContent = group.title;
            g.appendChild(t);
            renderNodes(group.nodes, g);
            canvasEl.appendChild(g);
        });

        if (model.rounds && model.rounds > 1) {
            roundBadge.style.display = "";
            roundBadge.textContent = "Round 1/" + model.rounds;
        } else {
            roundBadge.style.display = "none";
        }
        currentEl.textContent = "";
        document.getElementById("flow-toggle-badge").style.display = "";
        open();
    }

    // ── Live highlight ──
    function updateNode(id, status, ok) {
        var el = nodeEls[id];
        if (!el) return;
        var stateEl = el.querySelector(".flow-node-state");
        if (status === "running") {
            el.classList.remove("done-pass", "done-fail");
            el.classList.add("running");
            if (stateEl) stateEl.textContent = "\u2699";
            currentEl.textContent = "\u25B6 " + (nodeSummary[id] || id);
            try { el.scrollIntoView({ block: "nearest", behavior: "smooth" }); } catch (e) {}
        } else if (status === "done") {
            el.classList.remove("running");
            el.classList.add(ok ? "done-pass" : "done-fail");
            if (stateEl) stateEl.textContent = ok ? "\u2713" : "\u2717";
        }
    }

    function setRound(round, total) {
        if (total > 1) {
            roundBadge.style.display = "";
            roundBadge.textContent = "Round " + round + "/" + total;
        }
        // Reset the repeatable step nodes for the new round; keep setup/teardown.
        Object.keys(nodeEls).forEach(function (id) {
            if (id.indexOf("steps") === 0) {
                var el = nodeEls[id];
                el.classList.remove("running", "done-pass", "done-fail");
                var s = el.querySelector(".flow-node-state");
                if (s) s.textContent = "";
            }
        });
    }

    // ── Public event entry point (called from the chat poll loop) ──
    function handleEventLine(text) {
        if (typeof text !== "string") return false;
        if (text.indexOf("[Flow]") === 0) {
            try { render(JSON.parse(text.slice(6))); } catch (e) {}
            return true;
        }
        if (text.indexOf("[Node]") === 0) {
            try { var p = JSON.parse(text.slice(6)); updateNode(p.id, p.status, p.ok); } catch (e) {}
            return true;
        }
        if (text.indexOf("[Round]") === 0) {
            try { var q = JSON.parse(text.slice(7)); setRound(q.round, q.total); } catch (e) {}
            return true;
        }
        return false;
    }

    function isFlowEvent(text) {
        return typeof text === "string" &&
            (text.indexOf("[Flow]") === 0 || text.indexOf("[Node]") === 0 || text.indexOf("[Round]") === 0);
    }

    window.FlowPanel = {
        handleEventLine: handleEventLine,
        isFlowEvent: isFlowEvent,
        render: render,
        open: open,
        close: close,
    };

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", build);
    } else {
        build();
    }
})();
