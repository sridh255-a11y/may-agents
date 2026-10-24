// MAY Command Center - State & UI Controller
const state = {
    currentProject: "YouTube RankPeak",
    status: "idle",
    pendingApprovals: []
};

document.addEventListener('DOMContentLoaded', () => {
    initUI();
    setupListeners();
    pollStatus();
    loadApprovals();
});

function initUI() {
    updateOrbState("idle");
    console.log("MAY initialized");
}

function updateOrbState(newState) {
    const orb = document.getElementById('ai-orb');
    const orbText = document.getElementById('orb-status-text');
    orb.className = `ai-orb orb-${newState}`;
    orbText.innerText = newState.toUpperCase();
    state.status = newState;
}

function setupListeners() {
    document.getElementById('command-form').addEventListener('submit', (e) => {
        e.preventDefault();
        handleCommand();
    });

    document.querySelectorAll('.project-item').forEach(item => {
        item.addEventListener('click', () => {
            document.querySelectorAll('.project-item').forEach(i => i.classList.remove('active'));
            item.classList.add('active');
            state.currentProject = item.dataset.project;
            document.getElementById('current-project-label').innerText = state.currentProject;
            document.getElementById('active-project-tag').innerText = state.currentProject;
        });
    });
}

async function handleCommand() {
    const input = document.getElementById('command-input');
    const cmd = input.value;
    if (!cmd) return;

    updateOrbState("processing");
    input.value = "";

    try {
        const res = await fetch('/api/route', {
            method: 'POST',
            body: JSON.stringify({
                project: state.currentProject,
                request_type: "Research", // Default
                payload: { command: cmd }
            })
        });
        const data = await res.json();
        showToast("Request queued: " + data.request_id);
        updateOrbState("success");
        setTimeout(() => updateOrbState("idle"), 2000);
        loadApprovals();
    } catch (e) {
        showToast("Error executing command", "error");
        updateOrbState("error");
    }
}

async function loadApprovals() {
    try {
        const res = await fetch('/api/approvals');
        const data = await res.json();
        state.pendingApprovals = data.approvals;
        renderApprovals();
    } catch (e) {
        console.error("Failed to fetch approvals");
    }
}

function renderApprovals() {
    const container = document.getElementById('approvals-list');
    const badge = document.getElementById('pending-count-badge');
    badge.innerText = `${state.pendingApprovals.length} Pending`;

    if (state.pendingApprovals.length === 0) {
        container.innerHTML = '<p class="empty-msg">No pending requests requiring approval.</p>';
        return;
    }

    container.innerHTML = state.pendingApprovals.map(req => `
        <div class="approval-item">
            <span>${req.request_type} - ${req.project}</span>
            <button onclick="approve(${req.id})" class="btn-small approve">Approve</button>
            <button onclick="reject(${req.id})" class="btn-small reject">Reject</button>
        </div>
    `).join('');
}

async function approve(id) {
    await fetch('/api/approve', { method: 'POST', body: JSON.stringify({ id }) });
    loadApprovals();
    showToast("Approved " + id);
}

async function reject(id) {
    await fetch('/api/reject', { method: 'POST', body: JSON.stringify({ id }) });
    loadApprovals();
    showToast("Rejected " + id);
}

function showToast(msg, type = 'success') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.innerText = msg;
    container.appendChild(toast);
    setTimeout(() => toast.remove(), 3000);
}

async function pollStatus() {
    // In real app, poll periodically
}
