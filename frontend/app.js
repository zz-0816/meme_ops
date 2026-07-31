/**
 * meme_ops 前端交互逻辑
 *
 * 安全约束:
 * - 钱包连接 / 签名 / 交易确认均由用户主动触发
 * - 后端仅验证签名有效性，不接触私钥
 * - 生产环境需加入人机验证
 */

const API_BASE = window.MEME_OPS_API_BASE || (
    ['localhost', '127.0.0.1'].includes(window.location.hostname)
    && window.location.port === '3000'
        ? 'http://localhost:8788'
        : window.location.origin
);

function apiHeaders(json = false) {
    const headers = {};
    if (json) headers['Content-Type'] = 'application/json';
    if (state.token) headers.Authorization = `Bearer ${state.token}`;
    return headers;
}

// ============ 全局状态 ============
let state = {
    token: localStorage.getItem('meme_ops_token') || null,
    address: localStorage.getItem('meme_ops_address') || null,
    currentTab: 'overview',
    currentPersona: localStorage.getItem('meme_ops_persona') || 'operator',
    sidebarTab: 'history',
    currentProfile: null, // 正在查看的用户地址 (null = 自己)
    profileSection: 'posts',
    feedMode: localStorage.getItem('meme_ops_feed_mode') || 'recommended',
    posterDrafts: {},
    analysisJobs: new Map(),
    reportRequests: {},
    socialConnections: {},
    analysisDraft: (() => {
        try { return JSON.parse(localStorage.getItem('meme_ops_analysis_draft')) || {prompt:'', reportStyle:''}; }
        catch (error) { return {prompt:'', reportStyle:''}; }
    })(),
};

// ============ 初始化 ============
document.addEventListener('DOMContentLoaded', () => {
    // 清除可能已过期的 token
    if (state.token && !state.address) {
        localStorage.removeItem('meme_ops_token');
        state.token = null;
    }
    if (state.token && state.address) {
        document.getElementById('btnWallet').textContent = 'Connected';
        document.getElementById('btnWallet').classList.add('connected');
    }
    updateTopConnectionStatus();
    restoreAnalysisJobs();
    handleSocialReturn();
    window.addEventListener('hashchange', routeFromHash);
    routeFromHash();
});

// ============ 钱包连接 ============
async function connectWallet() {
    if (state.address) {
        // 已登录，断开
        logout();
        return;
    }

    if (!window.ethereum) {
        alert('MetaMask was not detected. Install the browser extension first.');
        return;
    }

    try {
        showLoading(true);
        const accounts = await window.ethereum.request({ method: 'eth_requestAccounts' });
        const address = accounts[0];

        // 获取 nonce
        const nonceResp = await fetch(`${API_BASE}/api/auth/nonce`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ address }),
        });
        const { nonce } = await nonceResp.json();

        // 用户签名（用户手动确认）
        const signature = await window.ethereum.request({
            method: 'personal_sign',
            params: [nonce, address],
        });

        // 登录
        const loginResp = await fetch(`${API_BASE}/api/auth/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ address, signature, nonce }),
        });
        if (!loginResp.ok) throw new Error('Sign-in failed');
        const { token } = await loginResp.json();

        state.token = token;
        state.address = address;
        localStorage.setItem('meme_ops_token', token);
        localStorage.setItem('meme_ops_address', address);
        const connectedProvider = (window.ethereum?.providers || [window.ethereum]).find(
            item => String(item?.selectedAddress || '').toLowerCase() === address.toLowerCase()
        );
        if (connectedProvider?.isOkxWallet || connectedProvider?.isOKExWallet) localStorage.setItem('meme_ops_wallet_provider', 'okx');
        else if (connectedProvider?.isBinance || connectedProvider?.isBinanceChain) localStorage.setItem('meme_ops_wallet_provider', 'binance');
        else if (connectedProvider?.isMetaMask) localStorage.setItem('meme_ops_wallet_provider', 'metamask');

        document.getElementById('btnWallet').textContent = 'Connected';
        document.getElementById('btnWallet').classList.add('connected');
        await updateTopConnectionStatus();

        routeFromHash();
    } catch (err) {
        if (err.code !== 4001) alert('Connection failed: ' + err.message);
    } finally {
        showLoading(false);
    }
}

function logout() {
    state.token = null;
    state.address = null;
    localStorage.removeItem('meme_ops_token');
    localStorage.removeItem('meme_ops_address');
    localStorage.removeItem('meme_ops_wallet_provider');
    document.getElementById('btnWallet').textContent = 'Connect Wallet';
    document.getElementById('btnWallet').classList.remove('connected');
    document.getElementById('walletAddr').style.display = 'none';
    state.analysisJobs.clear();
    state.socialConnections = {};
    localStorage.removeItem('meme_ops_analysis_jobs');
    updateTopConnectionStatus();
    switchTab('overview');
}

function injectedWalletInfo() {
    const remembered = localStorage.getItem('meme_ops_wallet_provider');
    if (remembered === 'okx') return {key:'okx', label:'OKX Wallet'};
    if (remembered === 'binance') return {key:'binance', label:'Binance Wallet'};
    if (remembered === 'metamask') return {key:'metamask', label:'MetaMask'};
    const candidates = window.ethereum?.providers?.length
        ? window.ethereum.providers
        : (window.ethereum ? [window.ethereum] : []);
    const provider = candidates.find(item => item.isOkxWallet || item.isOKExWallet)
        || candidates.find(item => item.isBinance || item.isBinanceChain)
        || candidates.find(item => item.isMetaMask)
        || candidates[0];
    if (provider?.isOkxWallet || provider?.isOKExWallet) return {key:'okx', label:'OKX Wallet'};
    if (provider?.isBinance || provider?.isBinanceChain) return {key:'binance', label:'Binance Wallet'};
    if (provider?.isMetaMask) return {key:'metamask', label:'MetaMask'};
    return {key:'wallet', label:'Web3 Wallet'};
}

function walletProviderLogo(provider) {
    if (provider === 'okx') {
        return `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 3h6v6H3V3Zm6 6h6v6H9V9Zm6-6h6v6h-6V3ZM3 15h6v6H3v-6Zm12 0h6v6h-6v-6Z"/></svg>`;
    }
    if (provider === 'binance') {
        return `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m12 2.8 3.2 3.2L12 9.2 8.8 6 12 2.8Zm5.4 5.4 3.2 3.2-3.2 3.2-3.2-3.2 3.2-3.2Zm-10.8 0 3.2 3.2-3.2 3.2-3.2-3.2 3.2-3.2ZM12 13.6l3.2 3.2L12 20l-3.2-3.2 3.2-3.2Zm0-6.2 4 4-4 4-4-4 4-4Z"/></svg>`;
    }
    if (provider === 'metamask') {
        return `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m20.8 3-7.5 5.6 1.4-3.3L20.8 3ZM3.2 3l7.4 5.7-1.3-3.4L3.2 3Zm14.9 12.8-2 3.1 4.3 1.2 1.2-4.2-3.5-.1ZM2.4 15.9l1.2 4.2 4.3-1.2-2-3.1-3.5.1Zm5.2-5.2-1.2 1.8 4.2.2-.1-4.5-2.9 2.5Zm8.8 0-3-2.5-.1 4.5 4.2-.2-1.1-1.8Zm-8.5 8.2 2.6-1.3-2.2-1.7-.4 3Zm5.6-1.3 2.6 1.3-.4-3-2.2 1.7Zm-3 0 .1-2.6-2.2.7 2.1 1.9Zm3 0 2.1-1.9-2.2-.7.1 2.6Zm-2.9-4.9-4.2-.2 1.9 3.4 2.2-.7.1-2.5Zm6.9-.2-4.2.2.1 2.5 2.2.7 1.9-3.4Zm-7 5.1-2.6 1.3 2.1 1.6-.1-1.1.6-1.8Zm3 0 .6 1.8-.1 1.1 2.1-1.6-2.6-1.3Zm.6 3-2.1.6-2.1-.6.2 1.7h3.8l.2-1.7Z"/></svg>`;
    }
    return `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 6.5A2.5 2.5 0 0 1 5.5 4h12A2.5 2.5 0 0 1 20 6.5V8h1v9.5a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17.5v-11ZM18 8V6.5a.5.5 0 0 0-.5-.5h-12a.5.5 0 0 0 0 1H18v1Zm-2 4v4h5v-4h-5Zm2 1h1v2h-1v-2Z"/></svg>`;
}

function socialStatusLogo(provider) {
    if (provider === 'x') {
        return `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24h-6.657l-5.214-6.817-5.967 6.817H1.68l7.73-8.835L1.254 2.25h6.826l4.713 6.231 5.45-6.231Zm-1.161 17.52h1.833L7.084 4.126H5.117L17.083 19.77Z"/></svg>`;
    }
    return `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21.94 4.67 18.9 19.01c-.23 1.01-.83 1.26-1.68.78l-4.63-3.41-2.23 2.15c-.25.25-.46.46-.94.46l.33-4.72 8.59-7.76c.37-.33-.08-.52-.58-.19L7.14 13.01l-4.57-1.43c-.99-.31-1.01-.99.21-1.47L20.65 3.2c.83-.3 1.55.2 1.29 1.47Z"/></svg>`;
}

async function updateTopConnectionStatus() {
    const wallet = document.getElementById('walletStatusIcon');
    const xIcon = document.getElementById('xStatusIcon');
    const telegramIcon = document.getElementById('telegramStatusIcon');
    const button = document.getElementById('btnWallet');
    if (!wallet || !xIcon || !telegramIcon || !button) return;
    const walletInfo = injectedWalletInfo();
    wallet.innerHTML = walletProviderLogo(walletInfo.key);
    wallet.dataset.provider = walletInfo.key;
    wallet.classList.toggle('is-connected', Boolean(state.address));
    wallet.classList.toggle('is-disconnected', !state.address);
    wallet.title = state.address
        ? `${walletInfo.label} · ${shortenAddr(state.address)}`
        : `Connect ${walletInfo.label}`;
    button.style.display = state.address ? 'none' : '';
    xIcon.innerHTML = socialStatusLogo('x');
    telegramIcon.innerHTML = socialStatusLogo('telegram');
    for (const icon of [xIcon, telegramIcon]) {
        icon.classList.remove('is-connected');
        icon.classList.add('is-disconnected');
    }
    if (!state.token) return;
    try {
        const response = await fetch(`${API_BASE}/api/social/connections`, {headers:apiHeaders()});
        if (!response.ok) return;
        const data = await response.json();
        state.socialConnections = Object.fromEntries(
            (data.connections || []).map(item => [item.provider, item])
        );
        const xConnected = Boolean(state.socialConnections.x);
        const telegramConnected = Boolean(state.socialConnections.telegram);
        xIcon.classList.toggle('is-connected', xConnected);
        xIcon.classList.toggle('is-disconnected', !xConnected);
        telegramIcon.classList.toggle('is-connected', telegramConnected);
        telegramIcon.classList.toggle('is-disconnected', !telegramConnected);
        xIcon.title = xConnected ? `X · @${state.socialConnections.x.username || 'connected'}` : 'X is not connected';
        telegramIcon.title = telegramConnected ? `Telegram · @${state.socialConnections.telegram.username || 'connected'}` : 'Telegram is not connected';
    } catch (error) { /* status icons stay dimmed */ }
}

function handleWalletStatusClick() {
    if (!state.address) return connectWallet();
    document.getElementById('walletConnectionPopover')?.remove();
    const walletInfo = injectedWalletInfo();
    const popover = document.createElement('div');
    popover.id = 'walletConnectionPopover';
    popover.className = 'connection-popover';
    popover.innerHTML = `<strong>${escapeHtml(walletInfo.label)}</strong><code>${escapeHtml(shortenAddr(state.address))}</code><button class="btn-small danger-link" onclick="logout();document.getElementById('walletConnectionPopover')?.remove()">Disconnect</button>`;
    document.getElementById('topnavConnections').appendChild(popover);
    setTimeout(() => document.addEventListener('click', event => {
        if (!popover.contains(event.target) && event.target.id !== 'walletStatusIcon') popover.remove();
    }, {once:true}), 0);
}

function openSocialBinding(provider) {
    if (!state.token) return connectWallet();
    location.hash = '#/settings';
    setTimeout(() => {
        document.querySelector(`.social-connection-card[data-provider="${provider}"]`)?.scrollIntoView({behavior:'smooth', block:'center'});
    }, 120);
}

function handleSocialReturn() {
    const url = new URL(window.location.href);
    const provider = url.searchParams.get('social');
    const status = url.searchParams.get('status');
    if (!provider || !status) return;
    const reason = url.searchParams.get('reason');
    setTimeout(() => {
        if (status === 'connected') showToast(`${provider === 'x' ? 'X' : 'Telegram'} connected successfully.`);
        else alert(`${provider === 'x' ? 'X' : 'Telegram'} connection ${status}: ${reason || 'authorization was not completed'}`);
        updateTopConnectionStatus();
    }, 150);
    url.searchParams.delete('social');
    url.searchParams.delete('status');
    url.searchParams.delete('reason');
    history.replaceState(null, '', `${url.pathname}${url.search}${url.hash}`);
}

// ============ Tab 切换 ============
function routeFromHash() {
    const route = location.hash || '#/overview';
    const postMatch = route.match(/^#\/post\/(\d+)$/);
    if (postMatch) {
        state.currentTab = 'community';
        setActiveNav('community');
        document.getElementById('sidebar').classList.remove('visible');
        renderPostDetail(Number(postMatch[1]));
        return;
    }
    const userMatch = route.match(/^#\/user\/([^/]+)(?:\/(posts|nfts))?$/);
    if (userMatch) {
        state.currentProfile = decodeURIComponent(userMatch[1]);
        state.profileSection = userMatch[2] || 'posts';
        switchTab('profile', false, null);
        return;
    }
    if (route === '#/settings') {
        state.currentProfile = null;
        state.currentTab = 'profile';
        setActiveNav('profile');
        document.getElementById('sidebar').classList.remove('visible');
        renderProfileSettings();
        return;
    }
    if (route.startsWith('#/community')) return switchTab('community', false);
    if (route === '#/overview') return switchTab('overview', false);
    if (route === '#/analysis') return switchTab('analysis', false);
    if (route === '#/watchlist') return switchTab('watchlist', false, 'analysis');
    const ownProfileMatch = route.match(/^#\/profile(?:\/(posts|nfts|bookmarks))?$/);
    if (ownProfileMatch) {
        state.currentProfile = null;
        state.profileSection = ownProfileMatch[1] || 'posts';
        return switchTab('profile', false);
    }
    switchTab('analysis', false);
}

function setActiveNav(tab) {
    document.querySelectorAll('.topnav-tab').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.tab === tab);
    });
}

function switchTab(tab, updateHash = true, navTab = tab) {
    state.currentTab = tab;
    if (tab !== 'profile' || updateHash) state.currentProfile = null;
    setActiveNav(navTab);
    const sidebar = document.getElementById('sidebar');
    sidebar.classList.toggle('visible', tab === 'analysis' || tab === 'watchlist');
    if (tab === 'analysis' || tab === 'watchlist') {
        renderSidebar();
    }
    if (updateHash) {
        const route = tab === 'profile' ? '#/profile' : `#/${tab}`;
        if (location.hash !== route) history.pushState(null, '', route);
    }

    switch (tab) {
        case 'overview': renderOverview(); break;
        case 'community': renderCommunity(); break;
        case 'analysis': renderAnalysis(); break;
        case 'watchlist': renderWatchlistPage(); break;
        case 'profile': renderProfile(); break;
    }
}

function switchSidebarTab(tab) {
    // 简化：只有自选，历史合并到自选点击
    renderWatchlist(document.getElementById('sidebarContent'));
}

// ============ 侧边栏：自选列表 ============
let editMode = false;
let compareMode = false;
let selectedWatchlist = new Set();
let currentHistoryContext = null;

async function renderSidebar() {
    return renderWatchlist(document.getElementById('sidebarContent'));
}

async function renderWatchlist(el) {
    if (!state.token) {
        el.innerHTML = '<div class="sidebar-empty">Connect a wallet to view your watchlist.</div>';
        return;
    }
    try {
        const resp = await fetch(`${API_BASE}/api/watchlist`, {headers: apiHeaders()});
        const data = await resp.json();
        const items = data.items || [];
        // 只在退出编辑模式时清空选择
        if (!editMode && !compareMode) selectedWatchlist.clear();

        const toolbar = editMode || compareMode
            ? `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                <span class="edit-toggle" onclick="${compareMode ? 'toggleCompareMode()' : 'toggleEditMode()'}" style="color:var(--red);cursor:pointer;">Cancel</span>
                <span id="selectedWatchlistCount" style="font-size:0.75rem;color:var(--text-muted);">Selected ${selectedWatchlist.size}</span>
               </div>`
            : `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                <span style="display:flex;gap:12px;">
                    <span class="edit-toggle" onclick="toggleEditMode()" style="cursor:pointer;">Edit</span>
                    <span class="edit-toggle compare-toggle" onclick="toggleCompareMode()" style="cursor:pointer;">Compare</span>
                </span>
                <span class="watchlist-count">${items.length} assets</span>
               </div>`;

        const listHtml = items.length === 0
            ? '<div class="sidebar-empty">Your watchlist is empty.<br>Analyze an asset and select the star to add it.</div>'
            : items.map(item => {
                const hasNote = item.notes && item.notes.trim();
                return `
                <div class="watchlist-item" draggable="${!editMode && !compareMode}" ondragstart="watchlistDragStart(event)" ondragover="event.preventDefault()" ondrop="watchlistDrop(event)" style="flex-direction:column;align-items:stretch;padding:6px 10px;border-radius:6px;margin-bottom:3px;" data-id="${item.id}">
                    <div style="display:flex;align-items:center;gap:8px;">
                        ${!editMode && !compareMode ? '<span class="drag-handle" aria-label="Drag to reorder">⠿</span>' : ''}
                        ${editMode || compareMode
                            ? `<input type="checkbox" id="wl_cb_${item.id}" onchange="toggleSelect(${item.id},this.checked)" ${selectedWatchlist.has(item.id)?'checked':''} style="flex-shrink:0;">`
                            : ''}
                        <span style="flex:1;cursor:pointer;font-size:0.85rem;font-weight:600;" onclick="${editMode || compareMode?'':`loadWatchlistHistory('${item.token_name.replace(/'/g,"\\'")}','${(item.chain||'?').replace(/'/g,"\\'")}',${item.id})`}">
                            ${item.token_name} <span style="font-size:0.7rem;color:var(--text-muted);font-weight:400;">[${item.chain||'?'}]</span>
                        </span>
                        ${!editMode && !compareMode ? `<span style="cursor:pointer;font-size:0.85rem;flex-shrink:0;" onclick="event.stopPropagation();startInlineEdit(${item.id},this)">📝</span>` : ''}
                    </div>
                    ${!editMode && !compareMode && hasNote ? `<div style="font-size:0.75rem;color:var(--accent);margin-top:3px;word-break:break-all;">${escapeHtml(item.notes)}</div>` : ''}
                </div>`;
            }).join('');

        const actionBar = editMode && selectedWatchlist.size > 0
            ? `<div style="display:flex;gap:8px;margin-top:8px;padding-top:8px;border-top:1px solid var(--border);">
                ${selectedWatchlist.size>0?`<button class="btn-small" style="flex:1;background:var(--red);color:white;" onclick="batchDeleteWatchlist()">Delete (${selectedWatchlist.size})</button>`:''}
               </div>`
            : compareMode
            ? `<div class="comparison-selection-actions">
                <span>Select 2–5 assets</span>
                <button class="btn-small" ${selectedWatchlist.size < 2 ? 'disabled' : ''} onclick="openComparisonPersonaDialog()">Create comparison (${selectedWatchlist.size})</button>
               </div>`
            : '';

        el.innerHTML = toolbar + `<div id="watchlistItems">${listHtml}</div>` + actionBar + `
            <div id="comparisonHistory" class="comparison-history"></div>
            <div style="margin-top:16px;padding-top:8px;border-top:1px solid var(--border);">
                <span style="font-size:0.7rem;color:var(--text-muted);cursor:pointer;" onclick="clearAllHistory()">Clear analysis history</span>
            </div>`;
        await renderComparisonHistory();
    } catch (e) {
        el.innerHTML = '<div class="sidebar-empty error-text">Unable to load watchlist.</div>';
    }
}

async function renderComparisonHistory() {
    const el = document.getElementById('comparisonHistory');
    if (!el || !state.token) return;
    try {
        const response = await fetch(`${API_BASE}/api/comparisons?limit=20`, {
            headers: apiHeaders(),
        });
        const data = await response.json();
        const records = data.records || [];
        el.innerHTML = `
            <div class="comparison-history-heading">
                <span>COMPARISON REPORTS</span><small>${records.length}</small>
            </div>
            ${records.length ? records.map(record => `
                <button class="comparison-history-item" onclick="loadComparisonDetail(${record.id})">
                    <strong>${escapeHtml(record.title)}</strong>
                    <span>${escapeHtml(personaLabel(record.persona))} · ${formatDate(record.created_at)}</span>
                    <i onclick="event.stopPropagation();deleteComparisonReport(${record.id})" aria-label="Delete comparison">×</i>
                </button>`).join('') : '<div class="comparison-history-empty">No comparison reports yet.</div>'}`;
    } catch (error) {
        el.innerHTML = '<div class="comparison-history-empty">Unable to load comparisons.</div>';
    }
}

function personaLabel(persona) {
    return {
        investor: 'Investor',
        operator: 'Community Operator',
        builder: 'Project Builder',
        researcher: 'Researcher',
    }[persona] || persona || 'Community Operator';
}

function openComparisonPersonaDialog() {
    if (selectedWatchlist.size < 2) return;
    document.getElementById('comparisonDialog')?.remove();
    document.body.insertAdjacentHTML('beforeend', `
        <div class="comparison-dialog-backdrop" id="comparisonDialog" onclick="if(event.target===this)closeComparisonPersonaDialog()">
            <section class="comparison-dialog">
                <header><div><span class="eyebrow">REPORT AGENT</span><h2>Create horizontal comparison</h2></div>
                <button onclick="closeComparisonPersonaDialog()" aria-label="Close">×</button></header>
                <p>All selected assets will be analyzed with the same perspective and compared dimension by dimension.</p>
                <label>Perspective
                    <select id="comparisonPersona">
                        <option value="operator">Community Operator</option>
                        <option value="investor">Investor</option>
                        <option value="builder">Project Builder</option>
                        <option value="researcher">Researcher</option>
                    </select>
                </label>
                <label>Comparison writing direction <small>optional</small>
                    <textarea id="comparisonStyle" maxlength="500" placeholder="Detailed horizontal comparison with evidence, strengths, weaknesses, and limitations"></textarea>
                </label>
                <footer><button class="btn-small" onclick="closeComparisonPersonaDialog()">Cancel</button>
                <button class="btn btn-primary" onclick="createComparisonReport()">Generate comparison</button></footer>
            </section>
        </div>`);
    document.getElementById('comparisonPersona').value = state.currentPersona;
}

function closeComparisonPersonaDialog() {
    document.getElementById('comparisonDialog')?.remove();
}

async function createComparisonReport() {
    const ids = [...selectedWatchlist];
    if (ids.length < 2 || ids.length > 5) return;
    const persona = document.getElementById('comparisonPersona')?.value || 'operator';
    const reportStyle = document.getElementById('comparisonStyle')?.value.trim()
        || 'Detailed horizontal comparison with evidence and limitations';
    closeComparisonPersonaDialog();
    showLoading(true);
    try {
        const response = await fetch(`${API_BASE}/api/comparisons`, {
            method: 'POST',
            headers: apiHeaders(true),
            body: JSON.stringify({
                watchlist_ids: ids,
                persona,
                report_style: reportStyle,
            }),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'Unable to generate comparison');
        compareMode = false;
        selectedWatchlist.clear();
        state.currentPersona = persona;
        await renderSidebar();
        const container = ensureWorkspaceResults();
        container.innerHTML = '';
        renderComparisonReport(data.report, data.comparison_id, new Date().toISOString());
    } catch (error) {
        alert(`Comparison failed: ${error.message}`);
    } finally {
        showLoading(false);
    }
}

async function loadComparisonDetail(comparisonId) {
    showLoading(true);
    try {
        const response = await fetch(`${API_BASE}/api/comparisons/${comparisonId}`, {
            headers: apiHeaders(),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'Comparison report not found');
        const container = ensureWorkspaceResults();
        container.innerHTML = '';
        renderComparisonReport(data.report, data.id, data.created_at);
    } catch (error) {
        alert(error.message);
    } finally {
        showLoading(false);
    }
}

async function deleteComparisonReport(comparisonId) {
    if (!confirm('Delete this comparison report?')) return;
    const response = await fetch(`${API_BASE}/api/comparisons/${comparisonId}`, {
        method: 'DELETE',
        headers: apiHeaders(),
    });
    if (!response.ok) return alert('Unable to delete comparison report.');
    await renderComparisonHistory();
}

function renderComparisonReport(report, comparisonId, createdAt) {
    const container = ensureWorkspaceResults();
    const assets = report.assets || [];
    const dimensions = report.dimension_comparison || [];
    const winner = report.winner || {};
    const modelLabel = report.generation_mode === 'deepseek'
        ? `${escapeHtml(report.generation_model || 'DeepSeek')} comparison`
        : 'Rules-engine comparison fallback';
    const gridColumns = `180px repeat(${Math.max(assets.length, 1)}, minmax(145px, 1fr))`;
    container.insertAdjacentHTML('afterbegin', `
        <section class="comparison-report" data-comparison-id="${comparisonId}">
            <header class="comparison-report-header">
                <div><span class="eyebrow">HORIZONTAL COMPARISON</span>
                    <h2>${escapeHtml(report.title || 'Asset comparison')}</h2>
                    <p>${escapeHtml(personaLabel(report.persona))} perspective · ${formatDate(createdAt || report.generated_at)} · ${modelLabel}</p>
                </div>
                <button class="back-link" onclick="backToMarketDiscovery()">← Back to workspace</button>
            </header>
            <div class="comparison-winner">
                <span>Highest score</span>
                <strong>${escapeHtml(winner.name || '—')} ${winner.score == null ? '' : `${Number(winner.score).toFixed(1)}/10`}</strong>
                <p>${escapeHtml(winner.reason || '')}</p>
            </div>
            <div class="comparison-summary">${escapeHtml(report.summary || '')}</div>
            ${(report.next_actions || []).length ? `<section class="comparison-next-actions"><h3>Next actions</h3><ol>${report.next_actions.map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ol></section>` : ''}
            <div class="comparison-matrix">
                <div class="comparison-matrix-row comparison-matrix-head" style="grid-template-columns:${gridColumns}">
                    <span>Dimension</span>
                    ${assets.map(asset => `<span><b>${escapeHtml(asset.name)}</b><small>${escapeHtml(asset.chain)}</small></span>`).join('')}
                </div>
                <div class="comparison-matrix-row overall-row" style="grid-template-columns:${gridColumns}">
                    <strong>Overall score</strong>
                    ${assets.map(asset => `<span><b>${Number(asset.overall_score || 0).toFixed(1)}</b><small>${escapeHtml(asset.risk_level || '')} risk</small></span>`).join('')}
                </div>
                ${dimensions.map(dimension => `
                    <div class="comparison-matrix-row" style="grid-template-columns:${gridColumns}">
                        <strong>${escapeHtml(dimension.dimension)}</strong>
                        ${(dimension.assets || []).map(asset => `
                            <span class="${asset.name === dimension.leader ? 'dimension-leader' : ''}">
                                <b>${Number(asset.score || 0).toFixed(1)}</b>
                                <small>${asset.name === dimension.leader ? 'Leader' : ''}</small>
                            </span>`).join('')}
                        <p>${escapeHtml(dimension.insight || '')}</p>
                    </div>`).join('')}
            </div>
            <div class="comparison-assets">
                ${assets.map(asset => `
                    <article>
                        <header><strong>${escapeHtml(asset.name)}</strong><span>${Number(asset.overall_score || 0).toFixed(1)}/10</span></header>
                        <h3>Strengths</h3>
                        <ul>${(asset.strengths || []).map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul>
                        <h3>Weaknesses</h3>
                        <ul>${(asset.weaknesses || []).map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul>
                    </article>`).join('')}
            </div>
        </section>`);
}

let draggedWatchlistId = null;
function watchlistDragStart(event) {
    draggedWatchlistId = Number(event.currentTarget.dataset.id);
    event.dataTransfer.effectAllowed = 'move';
}

async function watchlistDrop(event) {
    event.preventDefault();
    const targetId = Number(event.currentTarget.dataset.id);
    if (!draggedWatchlistId || draggedWatchlistId === targetId) return;
    const nodes = [...document.querySelectorAll('#watchlistItems .watchlist-item')];
    const ids = nodes.map(node => Number(node.dataset.id));
    const from = ids.indexOf(draggedWatchlistId);
    const to = ids.indexOf(targetId);
    ids.splice(to, 0, ids.splice(from, 1)[0]);
    await fetch(`${API_BASE}/api/watchlist/reorder`, {
        method: 'POST',
        headers: apiHeaders(true),
        body: JSON.stringify({ids}),
    });
    draggedWatchlistId = null;
    await renderSidebar();
    if (state.currentTab === 'watchlist') await renderWatchlistPage();
}

function toggleEditMode() {
    editMode = !editMode;
    compareMode = false;
    if (!editMode) selectedWatchlist.clear();
    renderSidebar();
}

function toggleCompareMode() {
    compareMode = !compareMode;
    editMode = false;
    selectedWatchlist.clear();
    renderSidebar();
}

function toggleSelect(id, checked) {
    if (checked && compareMode && selectedWatchlist.size >= 5) {
        document.getElementById(`wl_cb_${id}`).checked = false;
        alert('Compare up to 5 assets at a time.');
        return;
    }
    if (checked) selectedWatchlist.add(id);
    else selectedWatchlist.delete(id);
    // 不重新渲染，只更新计数
    const countEl = document.getElementById('selectedWatchlistCount');
    if (countEl) countEl.textContent = `Selected ${selectedWatchlist.size}`;
    // 更新底部操作栏
    renderWatchlist(document.getElementById('sidebarContent'));
}

async function startInlineEdit(itemId, iconEl) {
    const container = iconEl.parentElement;
    let currentNote = '';
    try {
        const resp = await fetch(`${API_BASE}/api/watchlist`, {headers: apiHeaders()});
        const data = await resp.json();
        const found = (data.items||[]).find(i => i.id === itemId);
        if (found) currentNote = found.notes || '';
    } catch(e) {}

    const input = document.createElement('input');
    input.type = 'text';
    input.value = currentNote;
    input.style.cssText = 'flex:1;padding:2px 6px;border:1px solid var(--accent);border-radius:4px;background:var(--bg-input);color:var(--text);font-size:0.8rem;min-width:0;';
    input.placeholder = 'Add a private note...';

    const save = async () => {
        const val = input.value.trim();
        if (val !== currentNote) {
            try {
                await fetch(`${API_BASE}/api/watchlist/${itemId}`, {
                    method: 'PATCH', headers: apiHeaders(true),
                    body: JSON.stringify({notes: val}),
                });
            } catch(e) {}
        }
        renderSidebar();
    };
    input.onblur = save;
    input.onkeydown = (e) => { if (e.key === 'Enter') { input.blur(); } };

    iconEl.replaceWith(input);
    input.focus();
}

function analyzeWatchlistItem(name, chain) {
    document.getElementById('analysisInput').value = `${name} ${chain}`;
    switchTab('analysis');
    submitAnalysis();
}

function normalizeChain(chain) {
    const aliases = {sol:'solana', solana:'solana', eth:'ethereum', ethereum:'ethereum', binance:'bsc'};
    const value = String(chain || 'unknown').trim().toLowerCase();
    return aliases[value] || value;
}

function recordMatchesTokenChain(record, tokenName, chain) {
    const recordName = String(record.token_name || '').trim().toLowerCase();
    const expectedName = String(tokenName || '').trim().toLowerCase();
    if (recordName !== expectedName) return false;
    let recordChain = record.chain;
    try {
        const report = typeof record.report_summary === 'string'
            ? JSON.parse(record.report_summary)
            : (record.report_summary || {});
        recordChain = (report.token || {}).chain || recordChain;
    } catch (e) {}
    return normalizeChain(recordChain) === normalizeChain(chain);
}

async function loadWatchlistHistory(tokenName, chain, watchlistId = null) {
    currentHistoryContext = {tokenName, chain, watchlistId};
    const el = ensureWorkspaceResults();
    el.innerHTML = '<div class="empty-state">Loading history...</div>';
    try {
        const resp = await fetch(`${API_BASE}/api/history?limit=200`, {headers: apiHeaders()});
        const data = await resp.json();
        const records = (data.records || []).filter(r => recordMatchesTokenChain(r, tokenName, chain));
        if (records.length === 0) {
            el.innerHTML = `
                <div class="result-section">
                    <h3>📋 ${tokenName} [${chain}]</h3>
                    <p style="color:var(--text-muted);padding:20px 0;">No analysis history for this asset.</p>
            </div>`;
            el.insertAdjacentHTML('afterbegin', '<button class="back-link history-back" onclick="backFromHistory()">← Back</button>');
            return;
        }
        el.innerHTML = `
            <div class="result-section">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
                    <h3>📋 ${tokenName} [${chain}] — ${records.length} reports</h3>
                    <span style="font-size:0.75rem;color:var(--red);cursor:pointer;" onclick="clearTokenHistory('${tokenName.replace(/'/g,"\\'")}','${chain.replace(/'/g,"\\'")}')">Clear</span>
                </div>
                <div style="display:flex;flex-direction:column;gap:6px;">
                    ${records.map(r => {
                        const personaMap = {investor:'🔍 Investor',operator:'📣 Community',builder:'🏗️ Builder',researcher:'📊 Research'};
                        return `
                        <div id="history-record-${r.id}" data-history-record style="display:flex;justify-content:space-between;align-items:center;padding:10px 14px;background:var(--bg-input);border-radius:8px;cursor:pointer;" onclick="loadHistoryDetail(${r.id})">
                            <div style="display:flex;align-items:center;gap:10px;">
                                <span style="font-size:0.8rem;color:var(--accent);">${personaMap[r.persona]||'🔍'}</span>
                                <span style="font-weight:600;">${r.overall_score ?? '?'}/10</span>
                                <span class="risk-badge ${r.risk_level||''}" style="font-size:0.7rem;padding:2px 8px;">${r.risk_level||'?'}</span>
                            </div>
                            <div style="display:flex;align-items:center;gap:12px;">
                                <span style="font-size:0.8rem;color:var(--text-muted);">${formatDate(r.created_at)}</span>
                                <span onclick="event.stopPropagation();deleteHistoryItem(${r.id},this)" style="color:var(--red);cursor:pointer;font-size:0.85rem;">✕</span>
                            </div>
                        </div>`;
                    }).join('')}
                </div>
            </div>
        `;
        el.insertAdjacentHTML('afterbegin', '<button class="back-link history-back" onclick="backFromHistory()">← Back</button>');
    } catch(e) {
        el.innerHTML = '<div class="empty-state error-text">Unable to load history.</div>';
    }
}

function backFromHistory() {
    currentHistoryContext = null;
    if (state.currentTab === 'watchlist') renderWatchlistPage();
    else backToMarketDiscovery();
}

async function batchDeleteWatchlist() {
    if (!confirm(`Delete ${selectedWatchlist.size} watchlist items?`)) return;
    try {
        const deletedIds = new Set(selectedWatchlist);
        const resp = await fetch(`${API_BASE}/api/watchlist/batch-delete`, {
            method:'POST', headers:apiHeaders(true),
            body:JSON.stringify({ids:[...selectedWatchlist]}),
        });
        if (!resp.ok) throw new Error('Delete failed');
        const viewingDeletedItem = currentHistoryContext?.watchlistId &&
            deletedIds.has(currentHistoryContext.watchlistId);
        editMode = false;
        selectedWatchlist.clear();
        await renderSidebar();
        if (viewingDeletedItem) {
            currentHistoryContext = null;
            if (state.currentTab === 'watchlist') await renderWatchlistPage();
            else renderAnalysis();
        } else if (state.currentTab === 'watchlist') {
            await renderWatchlistPage();
        }
    } catch(e) { alert('Delete failed'); }
}

async function loadHistoryDetail(id) {
    showLoading(true);
    try {
        const resp = await fetch(`${API_BASE}/api/analysis/${id}`, {headers: apiHeaders()});
        if (!resp.ok) throw new Error('Report not found');
        const data = await resp.json();
        const chartResp = await fetch(`${API_BASE}/api/charts/${id}`, {headers: apiHeaders()});
        if (chartResp.ok) {
            const chartData = await chartResp.json();
            data.charts = chartData.charts || {};
        }
        if (typeof data.report_summary === 'string') {
            try { data.report = JSON.parse(data.report_summary); } catch(e) {}
        } else {
            data.report = data.report_summary;
        }
        ensureWorkspaceResults().innerHTML = '';
        renderAnalysisResult(data);
    } catch (e) {
        if (currentHistoryContext) {
            loadWatchlistHistory(
                currentHistoryContext.tokenName,
                currentHistoryContext.chain,
                currentHistoryContext.watchlistId
            );
        } else {
            renderAnalysis();
        }
    }
    finally { showLoading(false); }
}

async function clearTokenHistory(tokenName, chain) {
    if (!confirm(`Clear all ${tokenName} [${chain}] reports?`)) return;
    try {
        const resp = await fetch(`${API_BASE}/api/history?limit=200`, {headers: apiHeaders()});
        const data = await resp.json();
        const ids = (data.records || [])
            .filter(r => recordMatchesTokenChain(r, tokenName, chain))
            .map(r => r.id);
        if (ids.length === 0) return;
        await fetch(`${API_BASE}/api/analysis/batch-delete`, {
            method:'POST', headers:apiHeaders(true),
            body:JSON.stringify({ids}),
        });
        loadWatchlistHistory(tokenName, chain, currentHistoryContext?.watchlistId);
    } catch(e) { alert('Unable to clear history'); }
}

async function deleteHistoryItem(id, trigger) {
    const row = trigger?.closest('[data-history-record]');
    if (row) row.remove();
    try {
        const resp = await fetch(`${API_BASE}/api/analysis/${id}`, {
            method: 'DELETE', headers: apiHeaders(),
        });
        if (!resp.ok) throw new Error('Delete failed');
        if (currentHistoryContext) {
            await loadWatchlistHistory(
                currentHistoryContext.tokenName,
                currentHistoryContext.chain,
                currentHistoryContext.watchlistId
            );
        }
        renderSidebar();
    } catch(e) {
        if (currentHistoryContext) {
            loadWatchlistHistory(
                currentHistoryContext.tokenName,
                currentHistoryContext.chain,
                currentHistoryContext.watchlistId
            );
        }
        alert('Delete failed. Try again.');
    }
}

async function clearAllHistory() {
    if (!confirm('Clear your entire analysis history? This cannot be undone.')) return;
    try {
        await fetch(`${API_BASE}/api/analysis/clear-all`, {
            method: 'POST', headers: apiHeaders(),
        });
        renderSidebar();
        ensureWorkspaceResults().innerHTML = '<div class="empty-state">Analysis history cleared.</div>';
    } catch(e) { alert('Unable to clear history'); }
}

async function deleteHistoryGroup(name, ids) {
    if (!confirm(`Delete all ${ids.length} reports for ${name}?`)) return;
    try {
        await fetch(`${API_BASE}/api/analysis/batch-delete`, {
            method: 'POST',
            headers: apiHeaders(true),
            body: JSON.stringify({ ids }),
        });
        renderSidebar();
    } catch (e) { alert('Delete failed'); }
}

// ============ 社区 Tab ============
let pendingPostImage = null;

async function renderCommunity() {
    const el = document.getElementById('mainContent');
    if (!state.address) {
        el.innerHTML = '<div class="empty-state"><p style="font-size:2rem;">👛</p><p>Connect a wallet to join the community.</p></div>';
        return;
    }
    if (!state.token) { el.innerHTML = '<div class="empty-state">Loading...</div>'; return; }

    el.innerHTML = `
        <div class="feed-shell">
            <header class="feed-sticky-header">
                <h2>Community <span>Explore</span></h2>
                <div class="feed-mode-tabs">
                    <button class="${state.feedMode === 'recommended' ? 'active' : ''}" onclick="setFeedMode('recommended')">For you</button>
                    <button class="${state.feedMode === 'following' ? 'active' : ''}" onclick="setFeedMode('following')">Following</button>
                </div>
            </header>
            <div id="feedContainer"><div class="empty-state">Loading...</div></div>
            <button class="compose-fab" onclick="openPostComposer()" aria-label="Create post" title="Create post">＋</button>
        </div>`;
    await loadFeed();
}

function openPostComposer() {
    if (document.getElementById('composeOverlay')) return;
    document.body.insertAdjacentHTML('beforeend', `
        <div class="compose-overlay" id="composeOverlay" onclick="if(event.target===this)closePostComposer()">
            <section class="compose-dialog">
                <header><button class="icon-button" onclick="closePostComposer()" aria-label="Close">×</button><strong>Create post</strong></header>
                <div class="compose-box" id="composeDropZone">
                    <textarea class="compose-input" id="composeInput" placeholder="What is happening in Meme markets?" maxlength="500"></textarea>
                    <div class="mention-suggestions" id="mentionSuggestions"></div>
                    <div id="composeImagePreview"></div>
                    <div class="compose-actions">
                        <div class="compose-tools">
                            <label class="compose-media" title="Upload PNG" aria-label="Upload PNG">▧
                                <input id="composeImageInput" type="file" accept=".png,image/png" onchange="handlePostImageFiles(this.files)">
                            </label>
                            <button class="compose-tool-button" onclick="insertMentionTrigger()" title="Mention someone" aria-label="Mention someone">@</button>
                        </div>
                        <span class="compose-char-count"><span id="charCount">0</span>/500</span>
                        <button class="btn btn-primary" onclick="submitPost()">Post</button>
                    </div>
                </div>
            </section>
        </div>`);
    const input = document.getElementById('composeInput');
    input.addEventListener('input', (event) => {
        document.getElementById('charCount').textContent = event.target.value.length;
        updateMentionSuggestions(event.target);
    });
    input.addEventListener('paste', handleComposerPaste);
    const dropZone = document.getElementById('composeDropZone');
    dropZone.addEventListener('dragover', event => {
        event.preventDefault();
        dropZone.classList.add('dragging');
    });
    dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragging'));
    dropZone.addEventListener('drop', event => {
        event.preventDefault();
        dropZone.classList.remove('dragging');
        handlePostImageFiles(event.dataTransfer.files);
    });
    input.focus();
}

function closePostComposer() {
    pendingPostImage = null;
    document.getElementById('composeOverlay')?.remove();
}

function insertMentionTrigger() {
    const input = document.getElementById('composeInput');
    if (!input) return;
    const position = input.selectionStart;
    input.value = input.value.slice(0, position) + '@' + input.value.slice(position);
    input.focus();
    input.selectionStart = input.selectionEnd = position + 1;
    input.dispatchEvent(new Event('input'));
}

function setFeedMode(mode) {
    state.feedMode = mode === 'following' ? 'following' : 'recommended';
    localStorage.setItem('meme_ops_feed_mode', state.feedMode);
    renderCommunity();
}

async function loadFeed() {
    try {
        const resp = await fetch(`${API_BASE}/api/posts?limit=20&mode=${state.feedMode}`, {
            headers: apiHeaders(),
        });
        if (resp.status === 401) {
            state.token = null;
            localStorage.removeItem('meme_ops_token');
            document.getElementById('feedContainer').innerHTML = '<div class="empty-state">Connect a wallet to continue.</div>';
            return;
        }
        const data = await resp.json();
        const el = document.getElementById('feedContainer');
        if (!data.posts?.length) {
            el.innerHTML = state.feedMode === 'following'
                ? '<div class="empty-state">Follow accounts to build your Following timeline.</div>'
                : '<div class="empty-state">No posts yet. Publish the first market observation.</div>';
            return;
        }
        el.innerHTML = data.posts.map(post => renderPost(post)).join('');
    } catch (e) {
        document.getElementById('feedContainer').innerHTML = '<div class="empty-state error-text">Unable to load the feed.</div>';
    }
}

function renderRichPostText(content) {
    return escapeHtml(content || '')
        .replace(
            /(^|\s)@([A-Za-z0-9_.-]{1,40})/g,
            '$1<button class="post-mention" onclick="event.stopPropagation();viewMention(\'$2\')">@$2</button>'
        )
        .replace(/\n/g, '<br>');
}

let mentionRequestId = 0;
async function updateMentionSuggestions(input) {
    const match = input.value.slice(0, input.selectionStart).match(/@([A-Za-z0-9_.-]{1,40})$/);
    const el = document.getElementById('mentionSuggestions');
    if (!match || !el) {
        if (el) el.innerHTML = '';
        return;
    }
    const requestId = ++mentionRequestId;
    const resp = await fetch(`${API_BASE}/api/users/search?q=${encodeURIComponent(match[1])}`, {
        headers: apiHeaders(),
    });
    const data = await resp.json();
    if (requestId !== mentionRequestId) return;
    el.innerHTML = (data.users || []).map(user => `
        <button type="button" onclick="insertMention('${String(user.nickname || shortenAddr(user.address)).replace(/'/g, "\\'")}')">
            <span>${avatarMarkup(user.avatar, (user.nickname || 'U')[0], user.nickname || '')}</span>
            <strong>${escapeHtml(user.nickname || shortenAddr(user.address))}</strong>
            <small>${shortenAddr(user.address)}</small>
        </button>`).join('');
}

function insertMention(name) {
    const input = document.getElementById('composeInput');
    const before = input.value.slice(0, input.selectionStart).replace(/@[A-Za-z0-9_.-]{1,40}$/, `@${name} `);
    input.value = before + input.value.slice(input.selectionStart);
    input.focus();
    input.selectionStart = input.selectionEnd = before.length;
    document.getElementById('mentionSuggestions').innerHTML = '';
}

async function viewMention(name) {
    const resp = await fetch(`${API_BASE}/api/users/search?q=${encodeURIComponent(name)}`, {headers: apiHeaders()});
    const data = await resp.json();
    const exact = (data.users || []).find(user => (user.nickname || '').toLowerCase() === name.toLowerCase());
    const user = exact || data.users?.[0];
    if (user) viewProfile(user.address);
}

function renderPost(p, detail = false) {
    const addr = shortenAddr(p.author);
    const name = p.author_nickname || addr;
    const initial = name[0].toUpperCase();
    const isOwnPost = state.address && p.author.toLowerCase() === state.address.toLowerCase();
    const avatar = avatarMarkup(p.author_avatar, initial, name);
    const stop = 'event.stopPropagation();';
    const imageEl = p.image_data
        ? `<button class="post-media" onclick="${stop}openImageViewer('${p.image_data.replace(/'/g, "\\'")}')"><img src="${p.image_data}" alt="Post attachment"></button>`
        : '';
    const attachedEl = p.attached_analysis_id ? `
        <button class="feed-attached-poster" onclick="${stop}loadHistoryDetail(${p.attached_analysis_id})">
            Analysis report #${p.attached_analysis_id} — ${p.overall_score ?? '?'}/10
        </button>` : '';
    const quotedEl = p.quoted_post_id ? `
        <div class="quoted-post feed-quoted-post">
            <strong>${escapeHtml(p.quoted_author_nickname || shortenAddr(p.quoted_author))}</strong>
            <span>@${shortenAddr(p.quoted_author)}</span>
            <p>${renderRichPostText(p.quoted_content || 'Original post deleted')}</p>
        </div>` : '';

    return `
        <article class="feed-post ${detail ? 'post-detail-card' : ''}" id="post-${p.id}" onclick="${detail ? '' : `openPostDetail(${p.id})`}">
            <div class="feed-post-header">
                <div class="feed-avatar">${avatar}</div>
                <div class="feed-identity">
                    <button class="feed-author" onclick="${stop}viewProfile('${p.author}')">${escapeHtml(name)}</button>
                    <div class="feed-time">@${addr} · ${formatDate(p.created_at)}</div>
                </div>
                ${isOwnPost ? `<button class="post-menu-delete" onclick="${stop}deleteOwnPost(${p.id})" title="Delete post">•••</button>` : ''}
            </div>
            <div class="feed-content">${renderRichPostText(p.content)}</div>
            ${imageEl}${attachedEl}${quotedEl}
            <div class="feed-actions">
                <button class="feed-action" onclick="${stop}openPostDetail(${p.id}, true)" aria-label="Reply">
                    <span class="action-icon">◯</span><span>${p.reply_count || 0}</span>
                </button>
                <button class="feed-action ${p.reposted ? 'reposted' : ''} ${isOwnPost ? 'disabled' : ''}" onclick="${stop}repostPost(${p.id}, this)" ${isOwnPost ? 'disabled title="You cannot repost your own post"' : ''} aria-label="Repost">
                    <span class="action-icon">↻</span><span>${p.repost_count || 0}</span>
                </button>
                <button class="feed-action ${p.liked ? 'liked' : ''}" onclick="${stop}toggleLike(${p.id}, this)" aria-label="Like">
                    <span class="action-icon">${p.liked ? '♥' : '♡'}</span><span>${p.like_count || 0}</span>
                </button>
                <span class="feed-action view-only" aria-label="${p.view_count || 0} views"><span class="action-icon">▥</span><span>${p.view_count || 0}</span></span>
                <button class="feed-action ${p.bookmarked ? 'bookmarked' : ''}" onclick="${stop}toggleBookmark(${p.id}, this)" aria-label="Bookmark"><span class="action-icon">${p.bookmarked ? '▰' : '▱'}</span></button>
                <button class="feed-action" onclick="${stop}sharePost(${p.id})" aria-label="Share"><span class="action-icon">↗</span></button>
                <button class="feed-action quote-action" onclick="${stop}quotePost(${p.id})" aria-label="Quote">Quote</button>
            </div>
        </article>`;
}

function handleComposerPaste(event) {
    const item = Array.from(event.clipboardData?.items || []).find(entry => entry.type === 'image/png');
    if (item) handlePostImageFiles([item.getAsFile()]);
}

function handlePostImageFiles(files) {
    const file = Array.from(files || [])[0];
    if (!file) return;
    if (file.type !== 'image/png' || (file.name && !file.name.toLowerCase().endsWith('.png'))) {
        return alert('Only PNG images are supported.');
    }
    if (file.size > 1024 * 1024) return alert('The PNG must be 1 MB or smaller.');
    const reader = new FileReader();
    reader.onload = () => {
        pendingPostImage = reader.result;
        document.getElementById('composeImagePreview').innerHTML = `
            <div class="compose-image-preview"><img src="${reader.result}" alt="PNG preview">
            <button onclick="removePostImage()" aria-label="Remove image">×</button></div>`;
    };
    reader.readAsDataURL(file);
}

function removePostImage() {
    pendingPostImage = null;
    const preview = document.getElementById('composeImagePreview');
    if (preview) preview.innerHTML = '';
    const input = document.getElementById('composeImageInput');
    if (input) input.value = '';
}

async function submitPost() {
    const input = document.getElementById('composeInput');
    const content = input.value.trim();
    if (!content && !pendingPostImage) return;
    try {
        showLoading(true);
        const resp = await fetch(`${API_BASE}/api/posts`, {
            method: 'POST',
            headers: apiHeaders(true),
            body: JSON.stringify({content, image_data: pendingPostImage}),
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || 'Unable to publish post');
        input.value = '';
        document.getElementById('charCount').textContent = '0';
        removePostImage();
        closePostComposer();
        await loadFeed();
    } catch (e) { alert(e.message); }
    finally { showLoading(false); }
}

async function toggleLike(postId, btn) {
    if (!state.address) { alert('Connect a wallet first.'); return; }
    try {
        const resp = await fetch(`${API_BASE}/api/posts/${postId}/like`, {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${state.token}` },
        });
        const data = await resp.json();
        btn.classList.toggle('liked', data.liked);
        btn.querySelector('.action-icon').textContent = data.liked ? '♥' : '♡';
        const count = btn.querySelectorAll('span')[1];
        const current = parseInt(count.textContent) || 0;
        count.textContent = data.liked ? current + 1 : Math.max(0, current - 1);
    } catch (e) { /* ignore */ }
}

async function repostPost(postId, btn) {
    if (!state.address) { alert('Connect a wallet first.'); return; }
    try {
        const resp = await fetch(`${API_BASE}/api/posts/${postId}/repost`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${state.token}`,
            },
            body: JSON.stringify({ quote_text: null }),
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || 'Repost failed');
        btn.classList.toggle('reposted', data.reposted);
        const count = btn.querySelectorAll('span')[1];
        const current = parseInt(count.textContent) || 0;
        count.textContent = data.reposted ? current + 1 : Math.max(0, current - 1);
    } catch (e) { alert(e.message); }
}

function quotePost(postId) {
    if (!state.address) return alert('Connect a wallet first.');
    const sourcePost = document.getElementById(`post-${postId}`);
    const authorName = sourcePost?.querySelector('.feed-author')?.textContent || 'User';
    const content = sourcePost?.querySelector('.feed-content')?.textContent || '';
    document.body.insertAdjacentHTML('beforeend', `
        <div class="quote-overlay" id="quoteOverlay" onclick="if(event.target===this)this.remove()">
            <div class="quote-dialog">
                <div class="quote-header"><strong>Quote post</strong><button onclick="document.getElementById('quoteOverlay').remove()">×</button></div>
                <textarea id="quoteInput" maxlength="500" placeholder="Add your comment"></textarea>
                <div class="quoted-post"><strong>${authorName}</strong><p>${content}</p></div>
                <div class="quote-footer"><span>Up to 500 characters</span><button class="btn btn-primary" onclick="submitQuote(${postId})">Quote</button></div>
            </div>
        </div>`);
    document.getElementById('quoteInput').focus();
}

async function submitQuote(postId) {
    const input = document.getElementById('quoteInput');
    const quoteText = input.value.trim();
    if (!quoteText) return;
    const resp = await fetch(`${API_BASE}/api/posts/${postId}/repost`, {
        method: 'POST',
        headers: {'Content-Type':'application/json', 'Authorization':`Bearer ${state.token}`},
        body: JSON.stringify({quote_text: quoteText}),
    });
    const data = await resp.json();
    if (!resp.ok) return alert(data.detail || 'Quote failed');
    document.getElementById('quoteOverlay')?.remove();
    routeFromHash();
}

async function deleteOwnPost(postId) {
    if (!confirm('Delete this post?')) return;
    const post = document.getElementById(`post-${postId}`);
    post?.remove();
    const resp = await fetch(`${API_BASE}/api/posts/${postId}`, {
        method: 'DELETE',
        headers: {'Authorization': `Bearer ${state.token}`},
    });
    if (!resp.ok) {
        alert('Delete failed');
        routeFromHash();
    }
}

async function sharePost(postId) {
    const url = `${location.origin}${location.pathname}#/post/${postId}`;
    try {
        await navigator.clipboard.writeText(url);
        alert('Post link copied');
    } catch (e) {
        prompt('Copy post link:', url);
    }
}

async function toggleBookmark(postId, btn) {
    const resp = await fetch(`${API_BASE}/api/posts/${postId}/bookmark`, {
        method: 'POST', headers: apiHeaders(),
    });
    const data = await resp.json();
    if (!resp.ok) return alert(data.detail || 'Unable to update bookmark');
    btn.classList.toggle('bookmarked', data.bookmarked);
    btn.querySelector('.action-icon').textContent = data.bookmarked ? '▰' : '▱';
}

function openPostDetail(postId) {
    location.hash = `#/post/${postId}`;
}

async function renderPostDetail(postId) {
    const el = document.getElementById('mainContent');
    if (!state.token) {
        el.innerHTML = '<div class="empty-state">Connect a wallet to view the conversation.</div>';
        return;
    }
    el.innerHTML = '<div class="empty-state">Loading conversation...</div>';
    try {
        const resp = await fetch(`${API_BASE}/api/posts/${postId}`, {headers: apiHeaders()});
        const post = await resp.json();
        if (!resp.ok) throw new Error(post.detail || 'Post not found');
        el.innerHTML = `
            <section class="feed-shell conversation-shell">
                <div class="conversation-header"><button onclick="history.back()" aria-label="Back">←</button><h2>Post</h2></div>
                ${renderPost(post, true)}
                <div class="reply-composer">
                    <textarea id="replyInput" maxlength="500" placeholder="Post your reply"></textarea>
                    <div><span>Join the conversation</span><button class="btn btn-primary" onclick="submitReply(${postId})">Reply</button></div>
                </div>
                <div class="reply-list">
                    ${(post.replies || []).length
                        ? post.replies.map(reply => renderPost(reply, true)).join('')
                        : '<div class="empty-state">No replies yet.</div>'}
                </div>
            </section>`;
    } catch (error) {
        el.innerHTML = `<div class="empty-state error-text">${escapeHtml(error.message)}</div>`;
    }
}

async function submitReply(postId) {
    const input = document.getElementById('replyInput');
    const content = input.value.trim();
    if (!content) return;
    const resp = await fetch(`${API_BASE}/api/posts/${postId}/replies`, {
        method: 'POST',
        headers: apiHeaders(true),
        body: JSON.stringify({content}),
    });
    const data = await resp.json();
    if (!resp.ok) return alert(data.detail || 'Unable to publish reply');
    renderPostDetail(postId);
}

function openImageViewer(src, title = 'Image') {
    document.body.insertAdjacentHTML('beforeend', `
        <div class="image-viewer" id="imageViewer" onclick="if(event.target===this)this.remove()">
            <button onclick="document.getElementById('imageViewer').remove()" aria-label="Close">×</button>
            <img src="${src}" alt="${escapeHtml(title)}">
        </div>`);
}

// ============ 分析 Tab ============
function renderOverview() {
    const el = document.getElementById('mainContent');
    document.getElementById('sidebar').classList.remove('visible');
    el.innerHTML = `
        <section class="overview-hero">
            <span class="eyebrow">OPS AGENT FOR MEME COMMUNITIES</span>
            <h1>Turn meme signals into an operating plan.</h1>
            <p>meme_ops separates four decision lenses. The default Community Operator
            agent finds narrative opportunities, states what the data cannot prove, and
            turns evidence into a seven-day community action plan.</p>
            <div class="overview-actions">
                ${state.token
                    ? `<button class="btn btn-primary" onclick="switchTab('analysis')">Open private workspace</button>`
                    : `<button class="btn btn-primary" onclick="connectWallet()">Connect wallet to analyze</button>`}
                <button class="btn btn-secondary" onclick="switchTab('community')">Explore community</button>
            </div>
        </section>
        <section class="overview-social">
            <div class="overview-social-heading">
                <div>
                    <span class="eyebrow">LIVE SOCIAL INTELLIGENCE</span>
                    <h2>Connect the communities you operate.</h2>
                    <p>Authorize read-only X and Telegram access so the Ops Agent can ground reports in current community signals. Connections remain private to this wallet.</p>
                </div>
                <span class="private-pill">Wallet private</span>
            </div>
            <div id="overviewSocialConnections">
                ${state.token
                    ? '<div class="social-connection-loading">Checking X and Telegram...</div>'
                    : `<div class="overview-social-gate">
                        ${socialProviderLogo('x')}
                        ${socialProviderLogo('telegram')}
                        <div><strong>Connect your wallet first</strong><span>Social accounts are stored per wallet and never shared with other users.</span></div>
                        <button class="btn btn-primary" onclick="connectWallet()">Connect wallet</button>
                    </div>`}
            </div>
        </section>
        <section class="overview-grid">
            <article><span>01</span><h3>Conclusion first</h3><p>See the role-specific verdict before supporting data.</p></article>
            <article><span>02</span><h3>Actionable operations</h3><p>Receive daily activities, dependencies, and measurable KPIs.</p></article>
            <article><span>03</span><h3>Honest evidence</h3><p>Disconnected sources stay neutral and are never presented as zero.</p></article>
            <article><span>04</span><h3>Private memory</h3><p>Your wallet keeps modules, report preferences, history, and watchlists private.</p></article>
        </section>`;
    if (state.token) loadSocialConnections('overviewSocialConnections', true);
}

async function renderAnalysis() {
    currentHistoryContext = null;
    renderAnalysisShell();
    loadTopMemes();
}

async function renderWatchlistPage() {
    currentHistoryContext = null;
    renderSidebar();
    const main = document.getElementById('mainContent');
    main.innerHTML = '<div id="watchlistResults"></div>';
    const el = document.getElementById('watchlistResults');
    if (!state.token) {
        el.innerHTML = '<div class="empty-state">Connect a wallet to open your private watchlist.</div>';
        return;
    }
    el.innerHTML = '<div class="market-loading">Loading your watchlist market data...</div>';
    try {
        const response = await fetch(`${API_BASE}/api/watchlist/market`, {headers: apiHeaders()});
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'Unable to load watchlist');
        const items = data.items || [];
        el.innerHTML = `
            <section class="market-board watchlist-board">
                <div class="market-board-header">
                    <div><span class="eyebrow">PRIVATE WATCHLIST</span><h2>Your tracked Meme assets</h2>
                    <p>Open an asset to review all reports saved for this token and chain.</p></div>
                    <span class="live-badge"><i></i> Wallet scoped · 60s market refresh</span>
                </div>
                ${items.length ? `<div class="market-table">
                    <div class="market-row market-table-head"><span>#</span><span>Asset</span><span>Price</span><span>24h</span><span>Market Cap</span><span></span></div>
                    ${items.map((item, index) => `
                        <button class="market-row" onclick="openWatchlistMarketHistory('${String(item.token_name).replace(/'/g,"\\'")}','${String(item.chain || 'unknown').replace(/'/g,"\\'")}',${Number(item.id)})">
                            <span class="market-rank">${index + 1}</span>
                            <span class="market-asset">${item.image ? `<img src="${item.image}" alt="">` : '<i>◇</i>'}<b>${escapeHtml(item.token_name)}</b><small>${escapeHtml(item.token_symbol || '')} · ${escapeHtml(item.chain || 'unknown')}</small></span>
                            <span>${formatPrice(item.price)}</span>
                            <span class="${Number(item.change_24h) >= 0 ? 'market-up' : 'market-down'}">${item.change_24h == null ? '—' : `${Number(item.change_24h).toFixed(2)}%`}</span>
                            <span>${formatCompactUsd(item.market_cap)}</span>
                            <span class="market-analyze">History →</span>
                        </button>`).join('')}
                </div>` : '<div class="empty-state">Your watchlist is empty. Analyze an asset and select the star to add it.</div>'}
            </section>`;
    } catch (error) {
        el.innerHTML = `<div class="empty-state error-text">${escapeHtml(error.message)}</div>`;
    }
}

function openWatchlistMarketHistory(name, chain, watchlistId) {
    loadWatchlistHistory(name, chain, watchlistId);
}

function renderAnalysisShell() {
    const el = document.getElementById('mainContent');
    el.innerHTML = `
        <div class="analysis-controls">
            <div class="persona-selector">
                <span>Perspective</span>
                <select id="personaSelect" onchange="state.currentPersona=this.value;localStorage.setItem('meme_ops_persona',this.value)">
                    <option value="operator">Community Operator</option>
                    <option value="investor">Investor</option>
                    <option value="builder">Project Builder</option>
                    <option value="researcher">Researcher</option>
                </select>
            </div>
            <div class="input-section">
                <div class="input-group">
                    <input id="analysisInput" ${state.token ? '' : 'disabled'} value="${escapeHtml(state.analysisDraft.prompt || '')}" placeholder="${state.token ? 'Meme name or name + chain (example: DOGE Solana)' : 'Connect a wallet to unlock private analysis'}" oninput="saveAnalysisDraft()" onkeydown="if(event.key==='Enter')submitAnalysis()" />
                    <button class="btn btn-primary" id="analysisBtn" onclick="${state.token ? 'submitAnalysis()' : 'connectWallet()'}">${state.token ? 'Analyze' : 'Connect wallet'}</button>
                </div>
                <label class="report-style-field" for="reportStyleInput">
                    <span>Report writing direction <small>optional</small></span>
                    <textarea id="reportStyleInput" ${state.token ? '' : 'disabled'} maxlength="500" oninput="saveAnalysisDraft()" placeholder="Example: friendly and concise, or academic with methodology and limitations">${escapeHtml(state.analysisDraft.reportStyle || '')}</textarea>
                </label>
            </div>
            <p class="analysis-hint">${state.token ? 'Short example: pepe sol. Click a Top 10 asset to analyze it from the selected perspective. Use + to add it to your comparison list.' : 'Rankings are public. Reports, learned modules, history, and comparisons unlock after wallet authentication.'}</p>
        </div>
        <div id="analysisResults"></div>
    `;
    document.getElementById('personaSelect').value = state.currentPersona;
    renderSidebar();
    renderAnalysisJobDock();
}

function saveAnalysisDraft() {
    state.analysisDraft = {
        prompt: document.getElementById('analysisInput')?.value || state.analysisDraft.prompt || '',
        reportStyle: document.getElementById('reportStyleInput')?.value || state.analysisDraft.reportStyle || '',
    };
    localStorage.setItem('meme_ops_analysis_draft', JSON.stringify(state.analysisDraft));
}

function ensureAnalysisResults() {
    if (!document.getElementById('analysisResults')) renderAnalysisShell();
    return document.getElementById('analysisResults');
}

function ensureWorkspaceResults() {
    if (state.currentTab === 'watchlist') {
        const main = document.getElementById('mainContent');
        let results = document.getElementById('watchlistResults');
        if (!results) {
            main.innerHTML = '<div id="watchlistResults"></div>';
            results = document.getElementById('watchlistResults');
        }
        return results;
    }
    return ensureAnalysisResults();
}

async function loadTopMemes() {
    const el = ensureAnalysisResults();
    el.innerHTML = '<div class="market-loading">Loading live Meme rankings...</div>';
    try {
        const resp = await fetch(`${API_BASE}/api/market/top-memes`);
        const data = await resp.json();
        const items = data.items || [];
        el.innerHTML = `
            <section class="market-board">
                <div class="market-board-header">
                    <div><span class="eyebrow">LIVE MARKET</span><h2>Top 10 Meme Assets</h2></div>
                    <span class="live-badge"><i></i> Updates every 60s</span>
                </div>
                <div class="market-table">
                    <div class="market-row market-table-head top-market-row"><span></span><span>#</span><span>Asset</span><span>Price</span><span>24h</span><span>Market Cap</span><span></span></div>
                    ${items.map(item => `
                        <button class="market-row top-market-row" onclick="analyzeTopMeme('${String(item.name).replace(/'/g,"\\'")}','${String(item.chain).replace(/'/g,"\\'")}')">
                            <span class="market-add" role="button" tabindex="0" aria-label="Add ${escapeHtml(item.name)} to comparison" onclick="event.stopPropagation();addTopMemeToCompare('${String(item.name).replace(/'/g,"\\'")}','${String(item.symbol || '').replace(/'/g,"\\'")}','${String(item.chain).replace(/'/g,"\\'")}')">+</span>
                            <span class="market-rank">${item.rank}</span>
                            <span class="market-asset">${item.image ? `<img src="${item.image}" alt="">` : '<i>◇</i>'}<b>${escapeHtml(item.name)}</b><small>${escapeHtml(item.symbol)} · ${escapeHtml(item.chain)}</small></span>
                            <span>${formatPrice(item.price)}</span>
                            <span class="${Number(item.change_24h) >= 0 ? 'market-up' : 'market-down'}">${item.change_24h == null ? '—' : `${Number(item.change_24h).toFixed(2)}%`}</span>
                            <span>${formatCompactUsd(item.market_cap)}</span>
                            <span class="market-analyze">Analyze →</span>
                        </button>`).join('')}
                </div>
            </section>`;
    } catch (e) {
        el.innerHTML = '<div class="empty-state error-text">Live market data is temporarily unavailable.</div>';
    }
}

function analyzeTopMeme(name, chain) {
    if (!state.token) {
        connectWallet();
        return;
    }
    document.getElementById('analysisInput').value = `${name} ${chain}`;
    submitAnalysis();
}

async function addTopMemeToCompare(name, symbol, chain) {
    if (!state.token) {
        await connectWallet();
        return;
    }
    try {
        const response = await fetch(`${API_BASE}/api/watchlist`, {
            method: 'POST',
            headers: apiHeaders(true),
            body: JSON.stringify({token_name: name, token_symbol: symbol, chain}),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'Unable to add asset');
        compareMode = true;
        selectedWatchlist.add(Number(data.id));
        await renderSidebar();
        showToast(data.duplicate
            ? `${name} is already tracked and selected for comparison.`
            : `${name} added and selected for comparison.`);
    } catch (error) {
        alert(error.message);
    }
}

function showToast(message) {
    const toast = document.createElement('div');
    toast.className = 'app-toast';
    toast.textContent = message;
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 2400);
}

function formatCompactUsd(value) {
    if (value == null) return '—';
    return '$' + Intl.NumberFormat('en-US', {notation:'compact', maximumFractionDigits:2}).format(value);
}

function formatPrice(value) {
    if (value == null) return '—';
    return '$' + Number(value).toLocaleString('en-US', {maximumSignificantDigits:6});
}

async function submitAnalysis() {
    const prompt = document.getElementById('analysisInput')?.value.trim();
    const reportStyle = document.getElementById('reportStyleInput')?.value.trim() || null;
    if (!prompt) return;
    if (!state.token) return alert('Connect your wallet before running a private analysis.');
    state.analysisDraft = {prompt, reportStyle: reportStyle || ''};
    localStorage.setItem('meme_ops_analysis_draft', JSON.stringify(state.analysisDraft));
    await startAnalysisJob({
        prompt,
        persona: state.currentPersona,
        report_style: reportStyle,
    });
}

function persistAnalysisJobs() {
    localStorage.setItem(
        'meme_ops_analysis_jobs',
        JSON.stringify([...state.analysisJobs.keys()])
    );
}

function restoreAnalysisJobs() {
    if (!state.token) return;
    let ids = [];
    try { ids = JSON.parse(localStorage.getItem('meme_ops_analysis_jobs') || '[]'); }
    catch (error) { ids = []; }
    ids.slice(0, 5).forEach(jobId => {
        state.analysisJobs.set(jobId, {
            job_id: jobId, status: 'loading', progress: 0, stage: 'Restoring analysis',
        });
        pollAnalysisJob(jobId);
    });
    renderAnalysisJobDock();
}

async function startAnalysisJob(request, options = {}) {
    const btn = document.getElementById('analysisBtn');
    if (btn) btn.disabled = true;
    try {
        const response = await fetch(`${API_BASE}/api/analysis/jobs`, {
            method: 'POST',
            headers: apiHeaders(true),
            body: JSON.stringify(request),
        });
        const job = await response.json();
        if (!response.ok) throw new Error(job.detail || 'Unable to start analysis');
        job.auto_open = state.currentTab === 'analysis';
        job.switching_from = options.switchingFrom || null;
        state.analysisJobs.set(job.job_id, job);
        persistAnalysisJobs();
        renderAnalysisJobDock();
        renderInlineAnalysisProgress(job);
        showToast(options.switchingFrom
            ? `Generating the ${personaLabel(request.persona)} perspective in the background.`
            : 'Analysis started. You can continue using other pages.');
        pollAnalysisJob(job.job_id);
        return job.job_id;
    } catch (error) {
        alert('Analysis failed: ' + error.message);
        return null;
    } finally {
        if (btn) btn.disabled = false;
    }
}

async function pollAnalysisJob(jobId) {
    if (!state.token || !state.analysisJobs.has(jobId)) return;
    try {
        const response = await fetch(`${API_BASE}/api/analysis/jobs/${jobId}`, {
            headers: apiHeaders(),
        });
        const job = await response.json();
        if (!response.ok) throw new Error(job.detail || 'Analysis job is unavailable');
        const previous = state.analysisJobs.get(jobId) || {};
        Object.assign(job, {
            auto_open: previous.auto_open,
            switching_from: previous.switching_from,
        });
        state.analysisJobs.set(jobId, job);
        renderAnalysisJobDock();
        renderInlineAnalysisProgress(job);
        if (['queued', 'running', 'cancelling', 'loading'].includes(job.status)) {
            setTimeout(() => pollAnalysisJob(jobId), 1100);
            return;
        }
        if (job.status === 'completed') {
            if (state.currentTab === 'analysis' && job.auto_open !== false) {
                showCompletedAnalysisJob(jobId);
            } else {
                showToast('Your analysis report is ready. Select View report when convenient.');
            }
            return;
        }
        if (job.status === 'failed') showToast(job.error || 'Analysis failed.');
        if (job.status === 'cancelled') setTimeout(() => dismissAnalysisJob(jobId), 1800);
    } catch (error) {
        const job = state.analysisJobs.get(jobId);
        if (job) {
            job.status = 'failed';
            job.stage = error.message;
            renderAnalysisJobDock();
            renderInlineAnalysisProgress(job);
        }
    }
}

function renderInlineAnalysisProgress(job) {
    if (state.currentTab !== 'analysis') return;
    const results = document.getElementById('analysisResults');
    if (!results || job.status === 'completed') return;
    let card = document.getElementById(`analysis-job-card-${job.job_id}`);
    if (!card) {
        results.insertAdjacentHTML('afterbegin', `<section class="analysis-job-card" id="analysis-job-card-${job.job_id}"></section>`);
        card = document.getElementById(`analysis-job-card-${job.job_id}`);
    }
    const terminal = ['failed', 'cancelled'].includes(job.status);
    card.innerHTML = `
        <div><span class="eyebrow">BACKGROUND ANALYSIS</span><strong>${escapeHtml(job.stage || 'Working')}</strong></div>
        <div class="analysis-job-progress"><i style="width:${Number(job.progress || 0)}%"></i></div>
        <span>${Number(job.progress || 0)}%</span>
        ${terminal
            ? `<button class="btn-small" onclick="dismissAnalysisJob('${job.job_id}')">Dismiss</button>`
            : `<button class="btn-small danger-link" onclick="cancelAnalysisJob('${job.job_id}')">Stop</button>`}`;
}

function renderAnalysisJobDock() {
    const dock = document.getElementById('analysisJobDock');
    if (!dock) return;
    const jobs = [...state.analysisJobs.values()];
    dock.classList.toggle('hidden', jobs.length === 0);
    dock.innerHTML = jobs.map(job => {
        const complete = job.status === 'completed';
        const terminal = ['failed', 'cancelled'].includes(job.status);
        return `<article class="analysis-job-dock-item ${complete ? 'is-ready' : ''}">
            <div><strong>${complete ? 'Report ready' : escapeHtml(job.stage || 'Analysis')}</strong><span>${complete ? personaLabel(job.source_request?.persona) : `${Number(job.progress || 0)}%`}</span></div>
            <div class="analysis-job-progress"><i style="width:${Number(job.progress || 0)}%"></i></div>
            ${complete
                ? `<button onclick="showCompletedAnalysisJob('${job.job_id}')">View report</button>`
                : terminal
                    ? `<button onclick="dismissAnalysisJob('${job.job_id}')">Dismiss</button>`
                    : `<button class="danger-link" onclick="cancelAnalysisJob('${job.job_id}')">Stop</button>`}
        </article>`;
    }).join('');
}

async function cancelAnalysisJob(jobId) {
    const job = state.analysisJobs.get(jobId);
    if (job) {
        job.status = 'cancelling';
        job.stage = 'Stopping safely';
        renderAnalysisJobDock();
        renderInlineAnalysisProgress(job);
    }
    try {
        await fetch(`${API_BASE}/api/analysis/jobs/${jobId}`, {
            method: 'DELETE', headers: apiHeaders(),
        });
        setTimeout(() => pollAnalysisJob(jobId), 250);
    } catch (error) {
        showToast('Unable to stop this analysis.');
    }
}

function dismissAnalysisJob(jobId) {
    state.analysisJobs.delete(jobId);
    document.getElementById(`analysis-job-card-${jobId}`)?.remove();
    persistAnalysisJobs();
    renderAnalysisJobDock();
}

function showCompletedAnalysisJob(jobId) {
    const job = state.analysisJobs.get(jobId);
    if (!job?.result) return pollAnalysisJob(jobId);
    state.currentTab = 'analysis';
    setActiveNav('analysis');
    document.getElementById('sidebar').classList.add('visible');
    if (location.hash !== '#/analysis') history.pushState(null, '', '#/analysis');
    if (!document.getElementById('analysisResults')) renderAnalysisShell();
    ensureAnalysisResults().innerHTML = '';
    renderAnalysisResult(job.result);
    dismissAnalysisJob(jobId);
    renderSidebar();
}

function renderAnalysisResult(data) {
    const report = data.report || data;
    const charts = data.charts || {};
    const token = report.token || {};
    const analysisId = data.analysis_id ?? data.id;
    const contractAddress = token.contract_addr || data.contract_addr || '';
    const sourceRequest = data.source_request || {
        prompt: data.prompt || token.raw_prompt || `${token.name || ''} ${token.chain || data.chain || ''}`.trim(),
        persona: report.persona || data.persona || state.currentPersona,
        report_style: data.report_style || report.request_intent?.style_instruction || null,
        token_name: data.token_name || token.name || null,
        contract_addr: contractAddress || null,
        chain: data.chain || token.chain || null,
    };
    state.reportRequests[analysisId] = sourceRequest;
    const container = ensureWorkspaceResults();
    const generationMode = report.generation_mode === 'deepseek'
        ? `${escapeHtml(report.generation_model || 'DeepSeek')} analysis`
        : 'Rules-engine fallback';
    const generationClass = report.generation_mode === 'deepseek' ? 'model-live' : 'model-fallback';
    document.querySelector('.analysis-controls')?.classList.add('results-mode');

    // ① 代币详情头部
    const icon = token.icon ? `<img src="${token.icon}" style="width:36px;height:36px;border-radius:50%;" onerror="this.style.display='none'">` : '🪙';
    const tokenHeader = `
        <div class="result-section" style="padding:14px 18px;">
            <div style="display:flex;align-items:center;gap:12px;">
                ${icon}
                <div style="flex:1;">
                    <div style="font-weight:700;font-size:1.05rem;">${token.name || '?'} ${token.symbol ? '('+token.symbol.toUpperCase()+')' : ''}</div>
                    <div style="font-size:0.8rem;color:var(--text-muted);">
                        ${token.chain || data.chain || '?'} · ${contractAddress
                            ? `<a class="contract-link" href="https://dexscreener.com/search?q=${encodeURIComponent(contractAddress)}" target="_blank" rel="noopener noreferrer" title="View ${contractAddress} on DexScreener">${shortenAddr(contractAddress)} ↗</a>`
                            : '—'}
                    </div>
                    <div class="model-status ${generationClass}" title="${report._llm_error ? escapeHtml(report._llm_error) : ''}">${generationMode}</div>
                    ${report.asset_match === 'reference-only' ? '<div class="asset-match-warning">No exact DEX pair was found on the requested chain; unrelated assets were excluded and reference data is shown.</div>' : ''}
                </div>
                <button class="btn-small" onclick="addToWatchlistFromResult('${(token.name||'').replace(/'/g,"\\'")}','${(token.chain||data.chain||'unknown').replace(/'/g,"\\'")}','${(token.symbol||'').replace(/'/g,"\\'")}','${contractAddress.replace(/'/g,"\\'")}')" title="Add to watchlist">⭐</button>
            </div>
        </div>`;

    // ② 文字分析卡片
    const recHtml = charts.recommendation_html || '';
    const recCard = recHtml
        ? `<div class="poster-card" style="margin-bottom:16px;">
            <iframe class="analysis-report-frame" srcdoc="${recHtml.replace(/"/g, '&quot;')}" onload="resizeAnalysisFrame(this)" scrolling="auto" title="Written analysis report"></iframe>
        </div>`
        : '';

    // ③ 三张海报横排
    const chartLabels = {
        investor: ['Trend Analysis', 'Meme Vitality', 'Allocation'],
        operator: ['Community Health', 'Growth Opportunities', '7-Day Plan'],
        builder: ['Project Health', 'Competitive Gap', 'Roadmap'],
        researcher: ['Sector Overview', 'Comparison Matrix', 'Risk Assessment'],
    };
    const labels = chartLabels[report.persona || 'operator'] || chartLabels.operator;
    const chartKeys = ['chart_1', 'chart_2', 'chart_3'];
    const chartImgs = chartKeys.some(k => charts[k])
        ? `<div class="poster-card" style="margin-bottom:16px;">
            <div style="display:flex;gap:12px;overflow-x:auto;">
                ${chartKeys.map((k, i) => charts[k]
                    ? `<div style="flex:1;min-width:200px;text-align:center;">
                        <div style="font-weight:600;font-size:0.85rem;margin-bottom:6px;color:var(--text);">${labels[i]}</div>
                        <img class="clickable-report-chart" src="${charts[k]}" onclick="openImageViewer(this.src,this.alt)" style="width:100%;border-radius:8px;" alt="${labels[i]}" title="Click to enlarge" />
                    </div>`
                    : '').join('')}
            </div>
        </div>`
        : '';

    const styleEditor = `
        <div class="poster-customizer">
            <label for="posterStyle_${analysisId}">Poster style</label>
            <textarea id="posterStyle_${analysisId}" maxlength="300" placeholder="Describe the visual style. Default when blank: cyberpunk"></textarea>
            <p class="poster-style-note">Your description controls the rendered scene, architecture, lighting, objects, and art direction. It is not printed verbatim. Verified metrics remain immutable.</p>
            <div id="posterProviderStatus_${analysisId}" class="poster-provider-status">Checking image renderer...</div>
            <div id="posterPreview_${analysisId}"></div>
            <div class="poster-actions">
                <span>Remove any unwanted block before minting your personal NFT.</span>
                <button class="btn-small" onclick="previewPosterNFT(${analysisId})">Preview NFT poster</button>
                <button class="btn btn-primary" onclick="mintPoster(${analysisId})">Mint Poster NFT · Pay Gas in Wallet</button>
            </div>
        </div>`;
    const reportPersona = report.persona || sourceRequest.persona || 'operator';
    const perspectiveSwitcher = `<div class="report-perspective-switcher" aria-label="Switch report perspective">
        <span>Perspective</span>
        ${[
            ['operator', 'Community Operator'],
            ['investor', 'Investor'],
            ['builder', 'Project Builder'],
            ['researcher', 'Researcher'],
        ].map(([key, label]) => `<button class="${reportPersona === key ? 'active' : ''}" ${reportPersona === key ? 'disabled' : ''} onclick="switchReportPerspective(${analysisId},'${key}')">${label}</button>`).join('')}
    </div>`;
    const card = `<section class="analysis-poster" data-analysis-id="${analysisId}">
        <div class="poster-toolbar"><button class="back-link" onclick="backToMarketDiscovery()">← Back to Top 10</button><strong>${escapeHtml(personaLabel(report.persona))} Report</strong><span>${formatDate(new Date().toISOString())}</span></div>
        <div class="editable-block">${tokenHeader}<button class="remove-block" onclick="removePosterBlock(this)" aria-label="Remove token details">×</button></div>
        ${recCard ? `<div class="editable-block">${recCard}<button class="remove-block" onclick="removePosterBlock(this)" aria-label="Remove written analysis">×</button></div>` : ''}
        ${chartImgs ? `<div class="editable-block">${chartImgs}<button class="remove-block" onclick="removePosterBlock(this)" aria-label="Remove charts">×</button></div>` : ''}
        ${styleEditor}
    </section>`;
    const orderedCard = `<section class="analysis-poster" data-analysis-id="${analysisId}">
        <div class="poster-toolbar"><button class="back-link" onclick="backToMarketDiscovery()">← Back to Top 10</button><strong>${escapeHtml(personaLabel(report.persona))} Report</strong><span>${formatDate(new Date().toISOString())}</span></div>
        ${perspectiveSwitcher}
        <div class="editable-block">${tokenHeader}<button class="remove-block" onclick="removePosterBlock(this)" aria-label="Remove token details">×</button></div>
        ${chartImgs ? `<div class="editable-block chart-first">${chartImgs}<button class="remove-block" onclick="removePosterBlock(this)" aria-label="Remove charts">×</button></div>` : ''}
        ${recCard ? `<div class="editable-block report-after-charts">${recCard}<button class="remove-block" onclick="removePosterBlock(this)" aria-label="Remove written analysis">×</button></div>` : ''}
        ${styleEditor}
    </section>`;
    container.insertAdjacentHTML('afterbegin', orderedCard);
    loadPosterProviderStatus(analysisId);
}

async function switchReportPerspective(analysisId, persona) {
    const source = state.reportRequests[analysisId];
    if (!source?.prompt) {
        return alert('The original report request is unavailable. Reopen this report from history and try again.');
    }
    state.currentPersona = persona;
    localStorage.setItem('meme_ops_persona', persona);
    await startAnalysisJob({
        ...source,
        persona,
    }, {switchingFrom: analysisId});
}

function backToMarketDiscovery() {
    if (currentHistoryContext) {
        loadWatchlistHistory(
            currentHistoryContext.tokenName,
            currentHistoryContext.chain,
            currentHistoryContext.watchlistId,
        );
        return;
    }
    renderAnalysisShell();
    loadTopMemes();
}

async function loadPosterProviderStatus(analysisId) {
    const el = document.getElementById(`posterProviderStatus_${analysisId}`);
    if (!el) return;
    try {
        const response = await fetch(`${API_BASE}/api/nft/image-provider`);
        const data = await response.json();
        const maximumBytes = data.onchain_metadata?.maximum_bytes;
        const storageNote = data.ipfs_configured
            ? 'Images and metadata will be pinned to IPFS before minting.'
            : `IPFS is not configured. Direct on-chain metadata is limited to ${maximumBytes ? maximumBytes.toLocaleString('en-US') : 'the configured maximum'} bytes and larger payloads are blocked.`;
        if (data.configured) {
            el.className = `poster-provider-status ${data.ipfs_configured ? 'configured' : 'missing'}`;
            el.textContent = `AI image renderer: ${data.provider} · ${data.model || 'configured model'}. ${storageNote}`;
        } else {
            el.className = 'poster-provider-status missing';
            el.textContent = `AI image renderer is not configured. The preview uses a deterministic SVG template: layout and copy may change, but it is not generative image rendering. ${storageNote}`;
        }
    } catch (error) {
        el.className = 'poster-provider-status missing';
        el.textContent = 'Unable to verify the AI image renderer. Template preview may be used.';
    }
}

function resizeAnalysisFrame(frame) {
    try {
        const height = frame.contentDocument?.documentElement?.scrollHeight ||
            frame.contentDocument?.body?.scrollHeight;
        if (height) frame.style.height = `${Math.max(640, height + 8)}px`;
    } catch (e) {
        frame.style.height = '900px';
    }
}

function removePosterBlock(button) {
    button.closest('.editable-block')?.remove();
}

async function previewPosterNFT(analysisId) {
    const style = document.getElementById(`posterStyle_${analysisId}`)?.value.trim() || 'Cyberpunk';
    const preview = document.getElementById(`posterPreview_${analysisId}`);
    preview.innerHTML = '<div class="market-loading">Generating poster preview...</div>';
    const response = await fetch(
        `${API_BASE}/api/nft/metadata/${analysisId}?poster_style=${encodeURIComponent(style)}`,
        {method: 'POST', headers: apiHeaders()},
    );
    const data = await response.json();
    if (!response.ok || !data.metadata?.image) {
        preview.innerHTML = `<div class="empty-state error-text">${escapeHtml(data.detail || 'Unable to generate poster preview.')}</div>`;
        return;
    }
    state.posterDrafts[analysisId] = {style, ...data};
    const provider = data.metadata.image_provider === 'template'
        ? 'Template preview · configure an image provider for generative art'
        : `${data.metadata.image_provider} · ${data.metadata.image_model}`;
    const plan = data.poster_plan || data.metadata.poster_plan || {};
    const planSummary = [
        plan.layout ? `Layout: ${plan.layout}` : '',
        plan.copy_density ? `Copy: ${plan.copy_density}` : '',
        plan.planner_model ? `Planner: ${plan.planner_model}` : '',
    ].filter(Boolean).join(' · ');
    preview.innerHTML = `
        <button class="poster-preview-button" onclick="openImageViewer('${data.metadata.image}','${escapeHtml(data.metadata.poster_id)}')">
            <img src="${data.metadata.image}" alt="${escapeHtml(data.metadata.poster_id)} preview">
        </button>
        <div class="poster-preview-id">${escapeHtml(data.metadata.poster_id)} · ${escapeHtml(provider)} · Click to enlarge</div>
        ${planSummary ? `<div class="poster-plan-summary">${escapeHtml(planSummary)}</div>` : ''}`;
}

async function addToWatchlistFromResult(name, chain, symbol, addr) {
    try {
        const resp = await fetch(`${API_BASE}/api/watchlist`, {
            method: 'POST',
            headers: apiHeaders(true),
            body: JSON.stringify({ token_name: name, chain, token_symbol: symbol, contract_addr: addr }),
        });
        const result = await resp.json();
        if (result.duplicate) {
            alert('This asset is already in your watchlist.');
        } else {
            const el = document.createElement('div');
            el.style.cssText = 'position:fixed;bottom:20px;right:20px;background:var(--green);color:var(--bg);padding:8px 16px;border-radius:8px;font-size:0.85rem;z-index:999;';
            el.textContent = 'Added to watchlist';
            document.body.appendChild(el);
            setTimeout(() => el.remove(), 2000);
        }
        await renderSidebar();
        if (state.currentTab === 'watchlist') await renderWatchlistPage();
    } catch (e) { alert('Action failed'); }
}

async function mintPoster(analysisId) {
    if (!state.address) { alert('Connect a wallet first.'); return; }
    if (!window.ethereum) { alert('MetaMask was not detected.'); return; }

    try {
        showLoading(true);

        // 1. 获取元数据
        if (!analysisId) throw new Error('Invalid analysis ID. Reopen the report.');
        const styleInput = document.getElementById(`posterStyle_${analysisId}`);
        const posterStyle = styleInput?.value.trim() || 'Cyberpunk';
        const metaResp = await fetch(`${API_BASE}/api/nft/metadata/${analysisId}?poster_style=${encodeURIComponent(posterStyle)}&pin=true`, {
            method: 'POST',
            headers: apiHeaders(),
        });
        const metaData = await metaResp.json();
        if (!metaResp.ok || !metaData.metadata) {
            throw new Error(metaData.detail || 'Unable to build NFT metadata');
        }
        const metadata = {...metaData.metadata, poster_style: posterStyle};
        const tokenURI = metaData.token_uri || JSON.stringify(metadata);
        const storage = metaData.storage || {mode: 'onchain-json'};

        // 2. 获取合约信息
        const contractResp = await fetch(`${API_BASE}/api/nft/contract`);
        const contract = await contractResp.json();
        if (!contractResp.ok || !contract.contract_address) {
            throw new Error('NFT contract configuration is unavailable');
        }
        if (/^0x0{40}$/i.test(contract.contract_address)) {
            throw new Error('The NFT contract is not deployed. Configure the testnet contract address first.');
        }

        const walletChainId = await window.ethereum.request({method: 'eth_chainId'});
        const expectedChainId = `0x${Number(contract.chain_id).toString(16)}`.toLowerCase();
        if (String(walletChainId).toLowerCase() !== expectedChainId) {
            throw new Error(`Switch your wallet to ${contract.chain} (Chain ID ${contract.chain_id}) before minting.`);
        }
        const bytecode = await window.ethereum.request({
            method: 'eth_getCode',
            params: [contract.contract_address, 'latest'],
        });
        if (!bytecode || bytecode === '0x') {
            throw new Error(`No NFT contract was found at ${contract.contract_address} on ${contract.chain}.`);
        }

        const transaction = {
            from: state.address,
            to: contract.contract_address,
            data: encodeMintData(tokenURI),
        };
        if (storage.mode === 'onchain-json' && storage.warning) {
            let estimatedGas = 'unavailable';
            try {
                const gasHex = await window.ethereum.request({
                    method: 'eth_estimateGas',
                    params: [transaction],
                });
                estimatedGas = Number.parseInt(gasHex, 16).toLocaleString('en-US');
            } catch (error) {}
            const proceed = confirm(
                `${storage.warning}\n\nEstimated Gas units: ${estimatedGas}\n\n` +
                'Direct on-chain image metadata is much more expensive than an IPFS URI. ' +
                'Continue to the wallet confirmation?'
            );
            if (!proceed) return;
        }

        // 3. 用户调用 MetaMask 发送交易
        const txHash = await window.ethereum.request({
            method: 'eth_sendTransaction',
            params: [transaction],
        });
        const receipt = await waitForTransactionReceipt(txHash);
        if (!receipt || receipt.status === '0x0') {
            throw new Error('The mint transaction reverted on-chain.');
        }
        const tokenId = await resolveMintedTokenId(receipt, contract.contract_address);

        // 4. 记录铸造结果
        const recordResp = await fetch(`${API_BASE}/api/nft/mint`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${state.token}`,
            },
            body: JSON.stringify({
                token_id: tokenId,
                tx_hash: txHash,
                analysis_id: analysisId,
                token_uri: tokenURI,
                contract_address: contract.contract_address,
                chain: contract.chain,
                poster_image: metaData.preview_image || state.posterDrafts[analysisId]?.metadata?.image || metadata.image,
                poster_style: posterStyle,
                poster_uid: metadata.poster_id,
            }),
        });
        const recordData = await recordResp.json();
        if (!recordResp.ok) throw new Error(recordData.detail || 'Minted on-chain, but the local NFT record could not be saved.');

        alert(`Poster NFT #${tokenId} minted successfully.\\nPoster ID: ${metadata.poster_id}\\nTx: ${txHash}`);
    } catch (err) {
        if (err.code !== 4001) {
            const message = String(err.message || 'Unknown error');
            const friendly = /insufficient funds/i.test(message)
                ? 'Your wallet does not have enough native MON on Monad Testnet to pay the mint Gas fee.'
                : message;
            alert('Mint failed: ' + friendly);
        }
    } finally {
        showLoading(false);
    }
}

function encodeMintData(tokenURI) {
    // mint(string) function selector: 0x + keccak256("mint(string)")[:8]
    const selector = '0xd85d3d27'; // precomputed
    const uriBytes = Array.from(new TextEncoder().encode(tokenURI));
    const uriHex = uriBytes
        .map(b => b.toString(16).padStart(2, '0')).join('');
    // offset (32 bytes for string location)
    const offset = '0000000000000000000000000000000000000000000000000000000000000020';
    // length (32 bytes)
    const len = uriBytes.length.toString(16).padStart(64, '0');
    // padded data (32-byte chunks)
    let data = uriHex;
    while (data.length % 64 !== 0) data += '0';
    return selector + offset + len + data;
}

async function waitForTransactionReceipt(txHash) {
    for (let attempt = 0; attempt < 80; attempt++) {
        const receipt = await window.ethereum.request({
            method: 'eth_getTransactionReceipt',
            params: [txHash],
        });
        if (receipt) return receipt;
        await new Promise(resolve => setTimeout(resolve, 1500));
    }
    throw new Error('The transaction is still pending. Check it in the block explorer before trying again.');
}

async function resolveMintedTokenId(receipt, contractAddress) {
    const normalized = contractAddress.toLowerCase();
    const mintLog = (receipt.logs || []).find(log =>
        String(log.address).toLowerCase() === normalized &&
        Array.isArray(log.topics) && log.topics.length === 3
    );
    if (mintLog?.topics?.[1]) return BigInt(mintLog.topics[1]).toString();

    const totalSupplyHex = await window.ethereum.request({
        method: 'eth_call',
        params: [{to: contractAddress, data: '0x18160ddd'}, 'latest'],
    });
    const supply = BigInt(totalSupplyHex || '0x0');
    if (supply === 0n) throw new Error('Unable to resolve the minted Token ID from the receipt.');
    return (supply - 1n).toString();
}

// ============ 个人主页 Tab ============
async function renderProfile() {
    const addr = state.currentProfile || state.address;
    if (!addr) {
        document.getElementById('mainContent').innerHTML =
            '<div class="empty-state"><p style="font-size:2rem;">👛</p><p>Connect a wallet to open Home.</p></div>';
        return;
    }

    const el = document.getElementById('mainContent');
    el.innerHTML = '<div class="empty-state">Loading...</div>';

    try {
        const [profileResp, postsResp, nftsResp] = await Promise.all([
            fetch(`${API_BASE}/api/users/${addr}`, {
                headers: state.token ? {'Authorization': `Bearer ${state.token}`} : {},
            }),
            fetch(`${API_BASE}/api/users/${addr}/posts?limit=20`, {
                headers: state.token ? {'Authorization': `Bearer ${state.token}`} : {},
            }),
            fetch(`${API_BASE}/api/users/${addr}/nfts`),
        ]);
        const profile = await profileResp.json();
        const postsData = await postsResp.json();
        const nftsData = await nftsResp.json();

        const name = profile.nickname || shortenAddr(addr);
        const isSelf = state.address && state.address.toLowerCase() === addr.toLowerCase();

        el.innerHTML = `
            <div class="profile-header">
                <div class="profile-top">
                    <div class="profile-avatar">${avatarMarkup(profile.avatar, name[0].toUpperCase(), name)}</div>
                    <div>
                        <div class="profile-name">${name}</div>
                        <div class="profile-address">${shortenAddr(addr)}</div>
                        ${profile.bio ? `<div class="profile-bio">${escapeHtml(profile.bio)}</div>` : ''}
                    </div>
                    ${!isSelf && state.address ? `
                        <button class="btn btn-small" style="margin-left:auto;" id="followBtn" onclick="toggleFollow('${addr}', this)">
                            ${profile.isFollowing ? 'Following' : 'Follow'}
                        </button>
                    ` : ''}
                </div>
                <div class="profile-stats">
                    <div class="stat-item"><div class="stat-value">${postsData.count || 0}</div><div class="stat-label">Posts</div></div>
                    <div class="stat-item" ${isSelf ? `onclick="viewFollows('${addr}','following')" style="cursor:pointer"` : ''}><div class="stat-value">${profile.following || 0}</div><div class="stat-label">Following</div></div>
                    <div class="stat-item" ${isSelf ? `onclick="viewFollows('${addr}','followers')" style="cursor:pointer"` : ''}><div class="stat-value">${profile.followers || 0}</div><div class="stat-label">Followers</div></div>
                    <div class="stat-item" onclick="openProfileSection('${addr}','nfts')" style="cursor:pointer"><div class="stat-value">${nftsData.count || 0}</div><div class="stat-label">Poster NFTs</div></div>
                </div>
            </div>

            ${isSelf ? `<button class="btn-small profile-edit-btn" onclick="location.hash='#/settings'">Edit profile</button>` : ''}
            <div class="profile-tabs">
                <button class="topnav-tab" data-profile-section="posts" onclick="openProfileSection('${addr}','posts')">Posts</button>
                <button class="topnav-tab" data-profile-section="nfts" onclick="openProfileSection('${addr}','nfts')">Poster NFTs</button>
                ${isSelf ? `<button class="topnav-tab" data-profile-section="bookmarks" onclick="openProfileSection('${addr}','bookmarks')">Bookmarks</button>` : ''}
            </div>
            <div id="profileContent"></div>
        `;

        if (state.profileSection === 'nfts') {
            renderProfileNFTs(addr, nftsData.nfts);
        } else if (state.profileSection === 'bookmarks' && isSelf) {
            renderProfileBookmarks();
        } else {
            state.profileSection = 'posts';
            renderProfilePosts(addr, postsData.posts);
        }
    } catch (e) {
        el.innerHTML = '<div class="empty-state error-text">Unable to load this profile.</div>';
    }
}

async function renderProfileSettings() {
    const el = document.getElementById('mainContent');
    if (!state.address || !state.token) {
        el.innerHTML = '<div class="empty-state">Connect a wallet first.</div>';
        return;
    }
    const meResp = await fetch(`${API_BASE}/api/auth/me`, {
        headers: {'Authorization': `Bearer ${state.token}`},
    });
    if (!meResp.ok) {
        el.innerHTML = '<div class="empty-state">Your session expired. Reconnect your wallet.</div>';
        return;
    }
    const me = await meResp.json();
    const fallback = (me.nickname || shortenAddr(state.address))[0].toUpperCase();
    el.innerHTML = `
        <section class="settings-page">
            <div class="settings-header">
                <button class="icon-button" onclick="history.back()" aria-label="Back">←</button>
                <div><h2>Edit profile</h2><p>Your wallet address identifies this account and cannot be changed.</p></div>
            </div>
            <div class="avatar-editor">
                <div class="profile-avatar" id="avatarPreview">${avatarMarkup(me.avatar, fallback, me.nickname || '')}</div>
                <div><strong>Profile picture</strong><p>Upload a PNG or choose a default Emoji avatar.</p></div>
            </div>
            <input type="hidden" id="settingsAvatar" value="${escapeHtml(me.avatar || '')}">
            <label class="settings-upload">Upload PNG
                <input type="file" accept=".png,image/png" onchange="handleAvatarUpload(this,'${fallback}')">
            </label>
            <div class="avatar-presets" aria-label="Default avatars">
                ${['🚀','🐸','🦊','🐶','🌙','⚡'].map(emoji => `<button type="button" onclick="selectDefaultAvatar('${emoji}')">${emoji}</button>`).join('')}
            </div>
            <label class="settings-field">Display name
                <input id="settingsNickname" maxlength="40" value="${escapeHtml(me.nickname || '')}" placeholder="Your display name">
            </label>
            <label class="settings-field">Bio <span>Optional</span>
                <textarea id="settingsBio" maxlength="240" placeholder="Share your research focus, project, or community role">${escapeHtml(me.bio || '')}</textarea>
            </label>
            <div class="readonly-wallet"><span>Wallet address</span><code>${escapeHtml(state.address)}</code></div>
            <section class="social-settings">
                <div class="social-settings-heading">
                    <div>
                        <strong>Social data connections</strong>
                        <p>Connect read-only X and Telegram identities to collect signals for assets outside the shared Top 100 universe.</p>
                    </div>
                    <span class="private-pill">Wallet private</span>
                </div>
                <div id="socialConnections"><div class="social-connection-loading">Loading connection status...</div></div>
                <p class="social-privacy-note">Access tokens are encrypted on the server. They are never shown to other users or returned to this browser.</p>
            </section>
            <div class="settings-actions">
                <button class="btn-small" onclick="location.hash='#/profile'">Cancel</button>
                <button class="btn btn-primary" onclick="saveProfileSettings()">Save profile</button>
            </div>
        </section>`;
    await loadSocialConnections('socialConnections');
}

function socialProviderLogo(provider) {
    if (provider === 'x') {
        return `<span class="social-brand-logo social-brand-x" aria-label="X">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24h-6.657l-5.214-6.817-5.967 6.817H1.68l7.73-8.835L1.254 2.25h6.826l4.713 6.231 5.45-6.231Zm-1.161 17.52h1.833L7.084 4.126H5.117L17.083 19.77Z"/></svg>
        </span>`;
    }
    return `<span class="social-brand-logo social-brand-telegram" aria-label="Telegram">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21.94 4.67 18.9 19.01c-.23 1.01-.83 1.26-1.68.78l-4.63-3.41-2.23 2.15c-.25.25-.46.46-.94.46l.33-4.72 8.59-7.76c.37-.33-.08-.52-.58-.19L7.14 13.01l-4.57-1.43c-.99-.31-1.01-.99.21-1.47L20.65 3.2c.83-.3 1.55.2 1.29 1.47Z"/></svg>
    </span>`;
}

function socialConfigurationMessage(providerName, provider, encryptionConfigured) {
    const missing = [];
    if (!encryptionConfigured) missing.push('SOCIAL_TOKEN_ENCRYPTION_KEY');
    if (providerName === 'x' && provider.client_id_configured === false) {
        missing.push('X_CLIENT_ID');
    }
    if (providerName === 'telegram') {
        if (provider.bot_username_configured === false) missing.push('TELEGRAM_BOT_USERNAME');
        if (provider.bot_token_configured === false) missing.push('TELEGRAM_BOT_TOKEN');
    }
    if (missing.length) return `Server setup required: ${missing.join(', ')}`;
    return providerName === 'x'
        ? 'X OAuth app is not configured'
        : 'Telegram bot is not configured';
}

function socialConnectionCards(data, prominent = false) {
    const byProvider = Object.fromEntries(
        (data.connections || []).map(item => [item.provider, item])
    );
    const x = byProvider.x;
    const telegram = byProvider.telegram;
    const provider = data.provider_status || {};
    const encryptionConfigured = Boolean(provider.encryption_configured);
    const xStatus = provider.x || {};
    const telegramStatus = provider.telegram || {};
    const xReady = encryptionConfigured && xStatus.oauth_configured;
    const telegramReady = encryptionConfigured && telegramStatus.login_configured;
    const xAction = x
        ? `<button class="btn-small danger-link" onclick="disconnectSocial('x')">Disconnect</button>`
        : xReady
            ? '<button class="btn-small social-connect-button" onclick="connectSocialX()">Connect X</button>'
            : '<span class="social-setup-badge">Setup required</span>';
    const telegramAction = telegram
        ? `<button class="btn-small danger-link" onclick="disconnectSocial('telegram')">Disconnect</button>`
        : telegramReady
            ? '<button class="btn-small social-connect-button telegram-connect" onclick="connectSocialTelegram()">Connect Telegram</button>'
            : `<div class="social-setup-actions">
                <span class="social-setup-badge">Setup required</span>
                <button class="social-help-link" onclick="openTelegramSetupGuide()">Setup guide →</button>
               </div>`;
    const communities = data.communities || [];
    return `
        <div class="social-connection-list ${prominent ? 'social-connection-list-prominent' : ''}">
            <article class="social-connection-card ${x ? 'is-connected' : ''}" data-provider="x">
                ${socialProviderLogo('x')}
                <div class="social-provider-copy">
                    <strong>X</strong>
                    <span>${x
                        ? `Connected as @${escapeHtml(x.username || x.provider_user_id)}`
                        : xReady
                            ? 'Ready to connect'
                            : escapeHtml(socialConfigurationMessage('x', xStatus, encryptionConfigured))}</span>
                    <small>Recent post volume, active authors, and public engagement signals.</small>
                </div>
                ${xAction}
            </article>
            <article class="social-connection-card ${telegram ? 'is-connected' : ''}" data-provider="telegram">
                ${socialProviderLogo('telegram')}
                <div class="social-provider-copy">
                    <strong>Telegram</strong>
                    <span>${telegram
                        ? `Connected as @${escapeHtml(telegram.username || telegram.provider_user_id)}`
                        : telegramReady
                            ? 'Ready to connect'
                            : escapeHtml(socialConfigurationMessage('telegram', telegramStatus, encryptionConfigured))}</span>
                    <small>${communities.length
                        ? `${communities.length} group/channel connection(s)`
                        : 'Connect your identity, then bind groups or channels for member signals.'}</small>
                </div>
                <div class="social-card-actions">
                    ${telegram ? '<button class="btn-small" onclick="createTelegramGroupCode()">Bind group</button>' : ''}
                    ${telegramAction}
                </div>
            </article>
        </div>`;
}

async function loadSocialConnections(targetId = 'socialConnections', prominent = false) {
    const el = document.getElementById(targetId);
    if (!el || !state.token) return;
    const resp = await fetch(`${API_BASE}/api/social/connections`, {
        headers: {'Authorization': `Bearer ${state.token}`},
    });
    if (!resp.ok) {
        el.innerHTML = '<div class="social-connection-error">Unable to load social connection status.</div>';
        return;
    }
    const data = await resp.json();
    el.innerHTML = socialConnectionCards(data, prominent);
    state.socialConnections = Object.fromEntries(
        (data.connections || []).map(item => [item.provider, item])
    );
    updateTopConnectionStatus();
}

async function socialApi(path, options = {}) {
    const resp = await fetch(`${API_BASE}${path}`, {
        ...options,
        headers: {
            ...(options.body ? {'Content-Type': 'application/json'} : {}),
            'Authorization': `Bearer ${state.token}`,
            ...(options.headers || {}),
        },
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.detail || 'Social connection request failed');
    return data;
}

async function connectSocialX() {
    try {
        showToast('Opening X authorization...');
        const data = await socialApi('/api/social/x/connect', {method: 'POST'});
        if (!data.authorization_url) throw new Error('X did not return an authorization URL');
        sessionStorage.setItem('meme_ops_social_pending', 'x');
        window.location.assign(data.authorization_url);
    } catch (error) {
        alert(error.message);
    }
}

async function connectSocialTelegram() {
    try {
        const data = await socialApi('/api/social/telegram/connect', {method: 'POST'});
        const overlay = document.createElement('div');
        overlay.className = 'quote-overlay';
        overlay.id = 'telegramLoginOverlay';
        overlay.onclick = event => {
            if (event.target === overlay) overlay.remove();
        };
        overlay.innerHTML = `
            <div class="quote-dialog social-login-dialog">
                <div class="quote-header">
                    <div><strong>Connect Telegram</strong><p>Telegram verifies your identity; no Telegram password is shared with meme_ops.</p></div>
                    <button onclick="document.getElementById('telegramLoginOverlay').remove()">×</button>
                </div>
                <div id="telegramWidgetMount"></div>
            </div>`;
        document.body.appendChild(overlay);
        const script = document.createElement('script');
        script.async = true;
        script.src = 'https://telegram.org/js/telegram-widget.js?22';
        script.setAttribute('data-telegram-login', data.bot_username);
        script.setAttribute('data-size', 'large');
        script.setAttribute('data-auth-url', data.callback_url);
        script.onerror = () => {
            document.getElementById('telegramWidgetMount').innerHTML = `
                <div class="social-connection-error">Telegram login could not load. Check browser content blocking or open the setup guide.</div>
                <button class="btn-small" onclick="openTelegramSetupGuide()">Open setup guide</button>`;
        };
        document.getElementById('telegramWidgetMount').appendChild(script);
    } catch (error) {
        alert(error.message);
    }
}

function openTelegramSetupGuide() {
    document.getElementById('telegramSetupOverlay')?.remove();
    const overlay = document.createElement('div');
    overlay.className = 'quote-overlay';
    overlay.id = 'telegramSetupOverlay';
    overlay.onclick = event => { if (event.target === overlay) overlay.remove(); };
    overlay.innerHTML = `
        <div class="quote-dialog social-login-dialog telegram-setup-guide">
            <div class="quote-header">
                <div><strong>Telegram connection setup</strong><p>Identity binding cannot work until the server can verify the Telegram login signature.</p></div>
                <button onclick="document.getElementById('telegramSetupOverlay').remove()">×</button>
            </div>
            <ol>
                <li>Open <strong>@BotFather</strong>, select your bot, then set its Domain to <code>${escapeHtml(window.location.hostname)}</code>.</li>
                <li>In Railway Variables set <code>TELEGRAM_BOT_USERNAME</code>, <code>TELEGRAM_BOT_TOKEN</code>, and <code>TELEGRAM_WEBHOOK_SECRET</code>.</li>
                <li>Set <code>APP_PUBLIC_URL</code> to <code>${escapeHtml(window.location.origin)}</code>, redeploy, then return and select Connect Telegram.</li>
            </ol>
            <div class="settings-actions">
                <a class="btn-small" href="https://core.telegram.org/widgets/login" target="_blank" rel="noopener noreferrer">Official login guide</a>
                <a class="btn btn-primary" href="https://t.me/BotFather" target="_blank" rel="noopener noreferrer">Open @BotFather</a>
            </div>
        </div>`;
    document.body.appendChild(overlay);
}

async function disconnectSocial(provider) {
    if (!confirm(`Disconnect ${provider === 'x' ? 'X' : 'Telegram'} from this wallet?`)) return;
    try {
        await socialApi(`/api/social/connections/${provider}`, {method: 'DELETE'});
        await loadSocialConnections();
        await updateTopConnectionStatus();
    } catch (error) {
        alert(error.message);
    }
}

async function createTelegramGroupCode() {
    try {
        const data = await socialApi('/api/social/telegram/link-code', {
            method: 'POST',
            body: JSON.stringify({}),
        });
        alert(`${data.instruction}\n\nThe code expires in 15 minutes.`);
    } catch (error) {
        alert(error.message);
    }
}

function avatarMarkup(avatar, fallback, label = '') {
    if (!avatar) return escapeHtml(fallback);
    if (avatar.startsWith('emoji:')) return `<span aria-label="${escapeHtml(label)}">${escapeHtml(avatar.slice(6))}</span>`;
    return `<img src="${escapeHtml(avatar)}" alt="${escapeHtml(label)}" onerror="this.replaceWith(document.createTextNode('${escapeHtml(fallback)}'))">`;
}

function selectDefaultAvatar(emoji) {
    const value = `emoji:${emoji}`;
    document.getElementById('settingsAvatar').value = value;
    document.getElementById('avatarPreview').innerHTML = `<span>${emoji}</span>`;
}

function handleAvatarUpload(input, fallback) {
    const file = input.files?.[0];
    if (!file) return;
    if (file.type !== 'image/png' || !file.name.toLowerCase().endsWith('.png')) {
        input.value = '';
        return alert('Only .png profile pictures are supported.');
    }
    if (file.size > 1024 * 1024) {
        input.value = '';
        return alert('The PNG must be 1 MB or smaller.');
    }
    const reader = new FileReader();
    reader.onload = () => {
        document.getElementById('settingsAvatar').value = reader.result;
        document.getElementById('avatarPreview').innerHTML =
            `<img src="${reader.result}" alt="Profile preview">`;
    };
    reader.onerror = () => alert('Unable to read this PNG.');
    reader.readAsDataURL(file);
}

async function saveProfileSettings() {
    const nickname = document.getElementById('settingsNickname').value.trim();
    const avatar = document.getElementById('settingsAvatar').value.trim();
    const bio = document.getElementById('settingsBio').value.trim();
    const resp = await fetch(`${API_BASE}/api/users/profile`, {
        method: 'PATCH',
        headers: {'Content-Type':'application/json', 'Authorization':`Bearer ${state.token}`},
        body: JSON.stringify({nickname, avatar, bio}),
    });
    if (!resp.ok) return alert('Unable to update profile');
    location.hash = '#/profile';
}

async function viewFollows(address, type) {
    if (!state.address || state.address.toLowerCase() !== address.toLowerCase()) {
        return;
    }
    const resp = await fetch(`${API_BASE}/api/users/${address}/${type}`, {
        headers: {'Authorization': `Bearer ${state.token}`},
    });
    if (!resp.ok) return alert('Unable to open this list');
    const data = await resp.json();
    const users = data[type] || [];
    document.getElementById('profileContent').innerHTML = users.length
        ? users.map(user => `<div class="follow-row" onclick="viewProfile('${user.address}')">
            <div class="feed-avatar">${(user.nickname || shortenAddr(user.address))[0].toUpperCase()}</div>
            <div><strong>${escapeHtml(user.nickname || shortenAddr(user.address))}</strong><div class="feed-time">${shortenAddr(user.address)}</div></div>
        </div>`).join('')
        : '<div class="empty-state">No users to show.</div>';
}

function renderProfilePosts(addr, posts = null) {
    state.profileSection = 'posts';
    setProfileSectionActive('posts');
    const el = document.getElementById('profileContent');
    if (posts && posts.length) {
        el.innerHTML = posts.map(p => renderPost(p)).join('');
    } else if (posts) {
        el.innerHTML = '<div class="empty-state">No posts yet.</div>';
    } else {
        fetch(`${API_BASE}/api/users/${addr}/posts?limit=50`, {
            headers: state.token ? {'Authorization': `Bearer ${state.token}`} : {},
        })
            .then(r => r.json())
            .then(d => renderProfilePosts(addr, d.posts));
    }
}

async function renderProfileNFTs(addr, nfts = null) {
    state.profileSection = 'nfts';
    setProfileSectionActive('nfts');
    const el = document.getElementById('profileContent');
    const render = items => {
            if (!items?.length) {
                el.innerHTML = '<div class="empty-state">No Poster NFTs yet.</div>';
                return;
            }
            const categories = [...new Set(items.map(item => item.category || item.token_name || 'Uncategorized'))];
            el.innerHTML = `${isSelf ? `
                <div class="nft-gallery-toolbar">
                    <label>Filter by asset
                        <select onchange="filterNFTCategory(this.value)">
                            <option value="">All assets</option>
                            ${categories.map(category => `<option value="${escapeHtml(category)}">${escapeHtml(category)}</option>`).join('')}
                        </select>
                    </label>
                    <span>Rename, classify, or hide a card here. Hiding does not burn the on-chain NFT.</span>
                </div>` : ''}
                <div class="nft-grid">
                ${items.map(n => {
                    const explorer = 'https://testnet.monadexplorer.com';
                    const image = n.poster_image || '';
                    const tokenLabel = n.token_id === 'pending' ? 'Pending' : `#${n.token_id}`;
                    const category = n.category || n.token_name || 'Uncategorized';
                    const title = n.display_name || n.poster_uid || 'Legacy Poster NFT';
                    return `
                    <div class="nft-card" data-nft-category="${escapeHtml(category)}">
                        ${image
                            ? `<button class="nft-image-button" onclick="openImageViewer('${image.replace(/'/g, "\\'")}','${escapeHtml(n.poster_uid || tokenLabel)}')"><img src="${image}" alt="${escapeHtml(n.poster_uid || tokenLabel)} poster"></button>`
                            : '<div class="nft-image-placeholder">Poster unavailable</div>'}
                        <div class="nft-card-body">
                            <div class="nft-token-id">${tokenLabel}</div>
                            <strong>${escapeHtml(title)}</strong>
                            <span>${escapeHtml(category)} · ${escapeHtml(n.poster_style || 'Cyberpunk')} · ${formatDate(n.created_at)}</span>
                            <div class="nft-links">
                                <a href="${explorer}/tx/${encodeURIComponent(n.tx_hash)}" target="_blank" rel="noopener noreferrer">Transaction ↗</a>
                                <a href="${explorer}/address/${encodeURIComponent(n.contract_address)}" target="_blank" rel="noopener noreferrer">Contract ↗</a>
                            </div>
                            ${isSelf ? `<div class="nft-owner-actions">
                                <button onclick="editNFTDisplay(${n.id},'${String(title).replace(/'/g,"\\'")}','${String(category).replace(/'/g,"\\'")}')">Rename / classify</button>
                                <button class="danger-link" onclick="hideNFTFromProfile(${n.id},this)">Hide</button>
                            </div>` : ''}
                        </div>
                    </div>`;
                }).join('')}
            </div>`;
        };
    let items = nfts;
    if (!items) {
        const response = await fetch(`${API_BASE}/api/users/${addr}/nfts`);
        items = (await response.json()).nfts;
    }
    const isSelf = state.address && addr.toLowerCase() === state.address.toLowerCase();
    if (isSelf) items = await syncLegacyNFTRecords(items || []);
    render(items);
}

function filterNFTCategory(category) {
    document.querySelectorAll('.nft-card[data-nft-category]').forEach(card => {
        card.classList.toggle('hidden', Boolean(category) && card.dataset.nftCategory !== category);
    });
}

async function editNFTDisplay(recordId, currentName, currentCategory) {
    const displayName = prompt('Poster name:', currentName);
    if (displayName === null) return;
    const category = prompt('Asset category:', currentCategory);
    if (category === null) return;
    const response = await fetch(`${API_BASE}/api/nft/${recordId}`, {
        method: 'PATCH',
        headers: apiHeaders(true),
        body: JSON.stringify({display_name: displayName, category}),
    });
    const data = await response.json();
    if (!response.ok) return alert(data.detail || 'Unable to update this poster.');
    renderProfileNFTs(state.address);
}

async function hideNFTFromProfile(recordId, button) {
    if (!confirm('Hide this Poster NFT from your profile? The on-chain token will remain in your wallet.')) return;
    const card = button.closest('.nft-card');
    card?.remove();
    const response = await fetch(`${API_BASE}/api/nft/${recordId}`, {
        method: 'DELETE',
        headers: apiHeaders(),
    });
    if (!response.ok) {
        alert('Unable to hide this poster.');
        renderProfileNFTs(state.address);
    }
}

async function syncLegacyNFTRecords(items) {
    if (!window.ethereum || !state.token || !items.some(item => item.token_id === 'pending')) return items;
    try {
        const chainId = await window.ethereum.request({method: 'eth_chainId'});
        if (String(chainId).toLowerCase() !== '0x279f') return items;
        for (const item of items.filter(nft => nft.token_id === 'pending' && nft.tx_hash)) {
            const receipt = await window.ethereum.request({
                method: 'eth_getTransactionReceipt', params: [item.tx_hash],
            });
            if (!receipt || receipt.status !== '0x1') continue;
            const tokenId = await resolveMintedTokenId(receipt, item.contract_address);
            const response = await fetch(`${API_BASE}/api/nft/${item.id}/confirm`, {
                method: 'PATCH', headers: apiHeaders(true), body: JSON.stringify({token_id: tokenId}),
            });
            if (response.ok) item.token_id = tokenId;
        }
    } catch (error) {
        // A legacy transaction may be from a different network or may have failed.
    }
    return items;
}

async function renderProfileBookmarks() {
    state.profileSection = 'bookmarks';
    setProfileSectionActive('bookmarks');
    const el = document.getElementById('profileContent');
    el.innerHTML = '<div class="empty-state">Loading bookmarks...</div>';
    const resp = await fetch(`${API_BASE}/api/bookmarks`, {headers: apiHeaders()});
    const data = await resp.json();
    if (!resp.ok) {
        el.innerHTML = '<div class="empty-state error-text">Unable to load bookmarks.</div>';
        return;
    }
    el.innerHTML = data.posts?.length
        ? data.posts.map(post => renderPost(post)).join('')
        : '<div class="empty-state">No bookmarks yet. Saved posts are private to your wallet.</div>';
}

function setProfileSectionActive(section) {
    document.querySelectorAll('[data-profile-section]').forEach(button => {
        button.classList.toggle('active', button.dataset.profileSection === section);
    });
}

function openProfileSection(address, section) {
    state.profileSection = section;
    const isSelf = state.address && address.toLowerCase() === state.address.toLowerCase();
    const route = isSelf
        ? `#/profile/${section}`
        : `#/user/${encodeURIComponent(address)}/${section}`;
    if (location.hash === route) {
        if (section === 'nfts') renderProfileNFTs(address);
        else if (section === 'bookmarks') renderProfileBookmarks();
        else renderProfilePosts(address);
    } else {
        location.hash = route;
    }
}

function viewProfile(address) {
    if (state.address && address.toLowerCase() === state.address.toLowerCase()) {
        location.hash = '#/profile';
    } else {
        location.hash = `#/user/${encodeURIComponent(address)}`;
    }
}

async function toggleFollow(address, btn) {
    try {
        const resp = await fetch(`${API_BASE}/api/users/${address}/follow`, {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${state.token}` },
        });
        const data = await resp.json();
        btn.textContent = data.following ? 'Following' : 'Follow';
    } catch (e) { /* ignore */ }
}

// ============ 辅助函数 ============
function showLoading(show) {
    document.getElementById('loadingOverlay').classList.toggle('hidden', !show);
}

function shortenAddr(addr) {
    if (!addr || addr.length < 10) return addr || '';
    return addr.slice(0, 6) + '...' + addr.slice(-4);
}

function formatDate(isoStr) {
    if (!isoStr) return '—';
    const d = new Date(isoStr);
    if (Number.isNaN(d.getTime())) return '—';
    const year = d.getFullYear();
    const month = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${year}/${month}/${day}`;
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}
