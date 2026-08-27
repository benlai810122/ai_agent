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
    var nodeData = {};      // id -> {function, type, arguments, condition}
    var editable = false;   // nodes editable only while a plan is pending
    var schemaCache = {};   // function name -> parameter JSON schema
    var editorEl = null;    // parameter-editor popover
    var bannerEl = null;
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
            '<div class="flow-edit-banner" id="flow-edit-banner" style="display:none">' +
                '\u270E Click any node to edit its parameters before running.' +
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
        bannerEl = document.getElementById("flow-edit-banner");

        document.getElementById("flow-close").addEventListener("click", close);
        document.getElementById("flow-popout").addEventListener("click", togglePopout);
        document.getElementById("flow-dock").addEventListener("click", toggleDock);

        // Parameter-editor popover (shared, positioned near the clicked node).
        editorEl = document.createElement("div");
        editorEl.id = "flow-editor";
        editorEl.className = "flow-hidden";
        document.body.appendChild(editorEl);
        document.addEventListener("mousedown", function (e) {
            if (editorEl.classList.contains("flow-hidden")) return;
            if (editorEl.contains(e.target)) return;
            if (e.target.closest && e.target.closest(".flow-node")) return;
            closeEditor();
        });

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
    function registerNode(el, node) {
        nodeEls[node.id] = el;
        nodeSummary[node.id] = node.type === "condition" ? ("IF " + node.function) : node.function;
        nodeData[node.id] = {
            "function": node.function,
            type: node.type,
            arguments: node.arguments || {},
            condition: node.condition || "",
            wrt_debug: !!node.wrt_debug,
        };
        el.addEventListener("click", function (e) {
            if (!editable) return;
            e.stopPropagation();
            openEditor(node.id, el);
        });
    }

    function renderNodes(nodes, container) {
        (nodes || []).forEach(function (node, idx) {
            if (idx > 0) {
                var conn = document.createElement("div");
                conn.className = "flow-connector";
                conn.textContent = "\u2193";
                container.appendChild(conn);
            }
            var num = node.id.split("-").pop();
            var hint = '<span class="flow-edit-hint">\u270E edit</span>';
            var wrtBadge = node.wrt_debug
                ? '<span class="flow-wrt-badge" title="Collect WRT logs if this step fails">WRT</span>'
                : "";
            if (node.type === "condition") {
                var el = document.createElement("div");
                el.className = "flow-node flow-condition";
                el.dataset.id = node.id;
                el.innerHTML =
                    '<div class="flow-node-index">' + esc(num) + "</div>" +
                    '<div class="flow-node-state"></div>' +
                    '<div class="flow-cond-label">IF &middot; ' + esc(node.condition || "") + "</div>" +
                    '<div class="flow-node-fn">' + esc(node.function) + wrtBadge + "</div>" + hint;
                container.appendChild(el);
                registerNode(el, node);

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
                    '<div class="flow-node-fn">' + esc(node.function) + wrtBadge + "</div>" +
                    (node.summary ? '<div class="flow-node-args">' + esc(node.summary) + "</div>" : "") + hint;
                container.appendChild(a);
                registerNode(a, node);
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
        nodeData = {};
        closeEditor();
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

        editable = !!model.editable;
        panel.classList.toggle("flow-editable", editable);
        if (bannerEl) bannerEl.style.display = editable ? "" : "none";
        open();
    }

    // Reset the panel to its empty state (e.g. when a planned test is cancelled).
    function clearChart() {
        model = null;
        nodeEls = {};
        nodeSummary = {};
        nodeData = {};
        editable = false;
        closeEditor();
        panel.classList.remove("flow-editable");
        if (bannerEl) bannerEl.style.display = "none";
        canvasEl.innerHTML =
            '<div class="flow-empty">No test flow yet.<br>Ask for a test plan and the flow chart will appear here.</div>';
        titleEl.textContent = "Test Flow";
        roundBadge.style.display = "none";
        currentEl.textContent = "";
        document.getElementById("flow-toggle-badge").style.display = "none";
    }

    // ── Live highlight ──
    function updateNode(id, status, ok) {        var el = nodeEls[id];
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

    // ── Node parameter editor ──
    function closeEditor() {
        if (editorEl) editorEl.classList.add("flow-hidden");
    }

    function fetchSchema(fn, cb) {
        if (schemaCache[fn]) { cb(schemaCache[fn]); return; }
        fetch("/tool_schema/" + encodeURIComponent(fn))
            .then(function (r) { return r.json(); })
            .then(function (j) { schemaCache[fn] = (j && j.schema) || {}; cb(schemaCache[fn]); })
            .catch(function () { cb({}); });
    }

    function openEditor(nodeId, anchorEl) {
        var d = nodeData[nodeId];
        if (!d) return;
        fetchSchema(d.function, function (schema) {
            buildEditor(nodeId, d, schema);
            positionEditor(anchorEl);
            editorEl.classList.remove("flow-hidden");
        });
    }

    function makeFieldWrapper(label, type, isReq) {
        var w = document.createElement("div");
        w.className = "flow-editor-field";
        var l = document.createElement("label");
        l.innerHTML = esc(label) +
            (isReq ? '<span class="flow-req">*</span>' : "") +
            (type ? '<span class="flow-type">' + esc(type) + "</span>" : "");
        w.appendChild(l);
        return w;
    }

    function buildEditor(nodeId, d, schema) {
        editorEl.innerHTML = "";
        var props = (schema && schema.properties) || {};
        var required = (schema && schema.required) || [];

        var head = document.createElement("div");
        head.className = "flow-editor-head";
        var title = document.createElement("div");
        title.className = "flow-editor-title";
        title.textContent = (d.type === "condition" ? "IF " : "") + d.function;
        var closeB = document.createElement("button");
        closeB.className = "flow-btn";
        closeB.textContent = "\u2715";
        closeB.addEventListener("click", closeEditor);
        head.appendChild(title);
        head.appendChild(closeB);
        editorEl.appendChild(head);

        // WRT Debug toggle: collect WRT logs into the report folder if this step fails.
        var wd = document.createElement("div");
        wd.className = "flow-editor-field flow-editor-check";
        var wc = document.createElement("input");
        wc.type = "checkbox";
        wc.id = "flow-wrt-check";
        wc.checked = !!d.wrt_debug;
        var wl = document.createElement("label");
        wl.setAttribute("for", "flow-wrt-check");
        wl.textContent = "Run WRT Debug if this step fails";
        wd.appendChild(wc);
        wd.appendChild(wl);
        editorEl.appendChild(wd);
        editorEl._wrtInput = wc;

        editorEl._conditionInput = null;
        if (d.type === "condition") {
            var cf = makeFieldWrapper("condition", "expression", false);
            var ci = document.createElement("input");
            ci.type = "text";
            ci.value = d.condition || "";
            cf.appendChild(ci);
            editorEl.appendChild(cf);
            editorEl._conditionInput = ci;
        }

        var fields = [];
        var keys = Object.keys(d.arguments || {});
        if (keys.length === 0 && d.type !== "condition") {
            var em = document.createElement("div");
            em.className = "flow-editor-empty";
            em.textContent = "This step has no parameters to edit.";
            editorEl.appendChild(em);
        }
        keys.forEach(function (key) {
            var spec = props[key] || {};
            var t = spec.type;
            var isReq = required.indexOf(key) >= 0;
            var wrap = makeFieldWrapper(key, t, isReq);
            var val = d.arguments[key];
            var input, kind;
            if (spec.enum && spec.enum.length) {
                input = document.createElement("select"); kind = "select";
                spec.enum.forEach(function (opt) {
                    var o = document.createElement("option");
                    o.value = String(opt); o.textContent = String(opt);
                    if (String(opt) === String(val)) o.selected = true;
                    input.appendChild(o);
                });
            } else if (t === "boolean") {
                input = document.createElement("input"); input.type = "checkbox"; kind = "boolean";
                input.checked = (val === true || String(val).toLowerCase() === "true");
                wrap.classList.add("flow-editor-check");
            } else if (t === "integer" || t === "number") {
                input = document.createElement("input"); input.type = "number"; kind = "number";
                if (t === "number") input.step = "any";
                input.value = (val == null ? "" : val);
            } else if (t === "array" || t === "object") {
                input = document.createElement("textarea"); kind = "json";
                try { input.value = JSON.stringify(val == null ? (t === "array" ? [] : {}) : val); }
                catch (e) { input.value = String(val); }
            } else {
                input = document.createElement("input"); input.type = "text"; kind = "text";
                input.value = (val == null ? "" : String(val));
            }
            wrap.appendChild(input);
            editorEl.appendChild(wrap);
            fields.push({ key: key, kind: kind, input: input });
        });

        var err = document.createElement("div");
        err.className = "flow-editor-error";
        editorEl.appendChild(err);

        var actions = document.createElement("div");
        actions.className = "flow-editor-actions";
        var remove = document.createElement("button");
        remove.className = "flow-remove";
        remove.textContent = "\u{1F5D1} Remove";
        remove.addEventListener("click", function () { removeStep(nodeId, err, remove); });
        var cancel = document.createElement("button");
        cancel.textContent = "Cancel";
        cancel.addEventListener("click", closeEditor);
        var save = document.createElement("button");
        save.className = "flow-save";
        save.textContent = "Save";
        save.addEventListener("click", function () { saveEdit(nodeId, d, fields, err, save); });
        actions.appendChild(remove);
        actions.appendChild(cancel);
        actions.appendChild(save);
        editorEl.appendChild(actions);
    }

    function removeStep(nodeId, errEl, removeBtn) {
        if (!window.confirm("Remove this step from the test?")) return;
        errEl.textContent = "";
        removeBtn.disabled = true;
        fetch("/remove_script_step", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ node_id: nodeId, rounds: (model && model.rounds) || 1 }),
        })
            .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
            .then(function (res) {
                removeBtn.disabled = false;
                if (!res.ok) { errEl.textContent = (res.j && res.j.error) || "Could not remove."; return; }
                closeEditor();
                if (res.j && res.j.flowchart) render(res.j.flowchart);
            })
            .catch(function () { removeBtn.disabled = false; errEl.textContent = "Network error removing step."; });
    }

    function saveEdit(nodeId, d, fields, errEl, saveBtn) {
        var args = {};
        fields.forEach(function (f) {
            args[f.key] = (f.kind === "boolean") ? f.input.checked : f.input.value;
        });
        var body = { node_id: nodeId, arguments: args, rounds: (model && model.rounds) || 1 };
        if (d.type === "condition" && editorEl._conditionInput) {
            body.condition = editorEl._conditionInput.value;
        }
        if (editorEl._wrtInput) {
            body.wrt_debug = editorEl._wrtInput.checked;
        }
        errEl.textContent = "";
        saveBtn.disabled = true;
        fetch("/update_script_step", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        })
            .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
            .then(function (res) {
                saveBtn.disabled = false;
                if (!res.ok) { errEl.textContent = (res.j && res.j.error) || "Could not save."; return; }
                closeEditor();
                if (res.j && res.j.flowchart) render(res.j.flowchart);
            })
            .catch(function () { saveBtn.disabled = false; errEl.textContent = "Network error saving changes."; });
    }

    function positionEditor(anchorEl) {
        var r = anchorEl.getBoundingClientRect();
        var w = 340, h = Math.min(window.innerHeight * 0.74, 380);
        var left = r.left - w - 12;
        if (left < 8) left = Math.max(8, Math.min(window.innerWidth - w - 8, r.right + 12));
        var top = Math.min(window.innerHeight - h - 8, Math.max(8, r.top));
        editorEl.style.left = left + "px";
        editorEl.style.top = top + "px";
    }

    // ── Public event entry point (called from the chat poll loop) ──
    function handleEventLine(text) {        if (typeof text !== "string") return false;
        if (text.indexOf("[FlowClear]") === 0) {
            clearChart();
            return true;
        }
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
            (text.indexOf("[Flow]") === 0 || text.indexOf("[Node]") === 0 ||
             text.indexOf("[Round]") === 0 || text.indexOf("[FlowClear]") === 0);
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
