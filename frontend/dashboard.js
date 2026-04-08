const API_BASE_URL = "/api/v1";
let sandboxSseConnection = null; // 新增：用于沙盘模型状态的实时流
window.boundDeviceMap = { edge: null, cloud: null };

// 👇 新增 1：开启沙盘模型实时流
function startSandboxModelStream() {
    if (!localStorage.getItem('jwt_token')) return;

    // 按需求：暂时只为普通用户开发，管理员直接提示
    const role = localStorage.getItem('user_role');
    if (role === 'admin') {
        document.getElementById('active-models-container').innerHTML =
            '<span style="color: #8b949e; font-size: 0.9rem;">管理员沙盘视图暂未开发，当前仅展示普通用户分配视角。</span>';
        return;
    }

    if (sandboxSseConnection) sandboxSseConnection.close();

    // 连接后端的 SSE 流接口
    sandboxSseConnection = new EventSource(`${API_BASE_URL}/models/stream`);

    sandboxSseConnection.onmessage = function(event) {
        const data = JSON.parse(event.data);
        renderSandboxModels(data.nodes || []);
    };

    sandboxSseConnection.onerror = function() {
        document.getElementById('active-models-container').innerHTML =
            '<span style="color: #ff7b72; font-size: 0.9rem;">⚠️ 状态同步断开，尝试重连中...</span>';
    };
}

// 👇 新增 2：提取并去重渲染模型徽章
function renderSandboxModels(nodes) {
    const container = document.getElementById('active-models-container');
    const availableModels = new Set();
    const boundEdgeDeviceId = window.boundDeviceMap.edge;
    const boundCloudDeviceId = window.boundDeviceMap.cloud;

    if (!boundEdgeDeviceId || !boundCloudDeviceId) {
        container.innerHTML = '<span style="color: #ff7b72; font-size: 0.9rem;">⚠️ 当前账号绑定设备信息不完整，无法判断可用协同模型</span>';
        return;
    }

    const groupedModels = new Map();

    nodes.forEach(node => {
        if (node.status !== 'online' || node.service_type !== 'runtime') {
            return;
        }

        if (node.device_id !== boundEdgeDeviceId && node.device_id !== boundCloudDeviceId) {
            return;
        }

        const candidateModels = Array.isArray(node.supported_models) && node.supported_models.length > 0
            ? node.supported_models
            : [node.model_key || node.model_name];

        candidateModels.forEach(modelName => {
            if (!groupedModels.has(modelName)) {
                groupedModels.set(modelName, { edge: false, cloud: false });
            }

            const modelState = groupedModels.get(modelName);
            if (node.device_id === boundEdgeDeviceId && node.node_role === 'edge') {
                modelState.edge = true;
            }
            if (node.device_id === boundCloudDeviceId && node.node_role === 'cloud') {
                modelState.cloud = true;
            }
        });
    });

    groupedModels.forEach((modelState, modelName) => {
        if (modelState.edge && modelState.cloud) {
            availableModels.add(modelName);
        }
    });

    if (availableModels.size === 0) {
        container.innerHTML = '<span style="color: #8b949e; font-size: 0.9rem;">🚫 你的边端与云端当前没有同时在线的协同模型</span>';
        return;
    }

    // 渲染极简的模型标签 (Badge)
    container.innerHTML = '';
    availableModels.forEach(modelName => {
        const badge = document.createElement('div');
        badge.style.cssText = 'background: rgba(56, 189, 248, 0.1); color: var(--edge-color); border: 1px solid var(--edge-color); padding: 4px 12px; border-radius: 12px; font-size: 0.85rem; font-weight: bold; box-shadow: 0 0 8px rgba(56, 189, 248, 0.2);';
        badge.textContent = `🚀 ${modelName}`;
        container.appendChild(badge);
    });
}

// ==========================================
// 1. 核心 HTTP 拦截器 (自动带 Token)
// ==========================================
async function fetchWithAuth(url, options = {}) {
    const token = localStorage.getItem('jwt_token');
    if (!token) throw new Error("未登录");

    const headers = { 'Accept': 'application/json', 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}`, ...options.headers };
    const response = await fetch(url, { ...options, headers });

    if (response.status === 401 || response.status === 403) {
        const data = await response.json().catch(() => ({}));
        if (response.status === 401) { logout(); throw new Error("登录已过期"); }
        throw new Error(data.detail || "权限不足");
    }
    return response;
}

// ==========================================
// 2. 登录与登出逻辑
// ==========================================
async function handleLogin() {
    const user = document.getElementById("login-user").value;
    const pass = document.getElementById("login-pass").value;
    const errDiv = document.getElementById("login-error");

    if (!user || !pass) return errDiv.textContent = "账号密码不能为空";
    errDiv.textContent = "正在验证...";

    try {
        const res = await fetch(`${API_BASE_URL}/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username: user, password: pass })
        });

        if (!res.ok) throw new Error("账号或密码错误");
        const data = await res.json();

        localStorage.setItem('jwt_token', data.access_token);
        localStorage.setItem('current_user', data.username);
        localStorage.setItem('user_role', data.role);

        initializeDashboard();
    } catch (error) { errDiv.textContent = error.message; }
}

function logout() {
    localStorage.clear();
    window.boundDeviceMap = { edge: null, cloud: null };
    window.ipToDeviceMap = {};
    document.getElementById("login-overlay").style.display = "flex";
    document.getElementById("grafana-frame").src = "";
    document.getElementById("admin-panel-btn").style.display = "none";
    document.getElementById('active-models-container').innerHTML =
        '<span style="color: #8b949e; font-size: 0.9rem;">等待状态同步...</span>';
    // if(sseConnection) sseConnection.close(); // 退出时断开 SSE 实时流
    // 👇 新增：断开沙盘实时流
    if (sandboxSseConnection) sandboxSseConnection.close();
}

// ==========================================
// 3. 后台管理面板逻辑 (Admin CRUD)
// ==========================================
let systemDevicesMap = {};

function openAdminModal() {
    document.getElementById("admin-modal-overlay").style.display = "flex";
    refreshAllAdminData();
}

function closeAdminModal() {
    document.getElementById("admin-modal-overlay").style.display = "none";
    loadMyDevices();
}

async function refreshAllAdminData() {
    document.getElementById("add-user-msg").textContent = "";
    document.getElementById("add-dev-msg").textContent = "";
    await fetchDevices();
    await fetchUsers();
}

async function fetchDevices() {
    const tbody = document.getElementById("device-table-body");
    const cbContainer = document.getElementById("dynamic-device-checkboxes");
    try {
        const res = await fetchWithAuth(`${API_BASE_URL}/system/devices`);
        const devices = await res.json();
        tbody.innerHTML = ''; cbContainer.innerHTML = ''; systemDevicesMap = {};
        devices.forEach(d => {
            systemDevicesMap[d.id] = d.name;
            tbody.innerHTML += `<tr>
                <td style="font-weight:bold">${d.id}</td><td>${d.name}</td><td><code>${d.value}</code></td>
                <td>${d.id !== 'cloud' ? `<span class="delete-btn" onclick="deleteDevice('${d.id}')">下线</span>` : '<span style="color:#666">保留</span>'}</td>
            </tr>`;
            const isChecked = d.id === 'cloud' ? 'checked' : '';
            cbContainer.innerHTML += `<label><input type="checkbox" class="device-check" value="${d.id}" data-type="${d.type}" ${isChecked}> ${d.name}</label>`;
        });
    } catch (error) { cbContainer.innerHTML = '<span style="color:red">获取设备失败</span>'; }
}

async function createNewDevice() {
    const id = document.getElementById("new-dev-id").value;
    const name = document.getElementById("new-dev-name").value;
    const val = document.getElementById("new-dev-value").value;
    // 👇 获取选择的类型
    const type = document.getElementById("new-dev-type").value;
    const msg = document.getElementById("add-dev-msg");

    if (!id || !name || !val) return msg.innerHTML = '<span style="color:red">所有字段必填</span>';
    try {
        // 👇 body 中增加 device_type: type
        await fetchWithAuth(`${API_BASE_URL}/system/devices`, { method: 'POST', body: JSON.stringify({ id, name, value: val, device_type: type }) });
        msg.innerHTML = '<span style="color:var(--accent-green)">✅ 资产录入成功</span>';
        // 清空表单
        document.getElementById("new-dev-id").value = "";
        document.getElementById("new-dev-name").value = "";
        document.getElementById("new-dev-value").value = "";
        refreshAllAdminData();
    } catch (e) { msg.innerHTML = `<span style="color:red">❌ ${e.message}</span>`; }
}

async function deleteDevice(id) {
    if (!confirm(`警告：确定删除物理设备【${id}】吗？这会同步移除所有用户身上的该权限！`)) return;
    try { await fetchWithAuth(`${API_BASE_URL}/system/devices/${id}`, { method: 'DELETE' }); refreshAllAdminData(); } catch (e) { alert("删除失败: " + e.message); }
}

async function fetchUsers() {
    const tbody = document.getElementById("user-table-body");
    try {
        const res = await fetchWithAuth(`${API_BASE_URL}/users`);
        const users = await res.json();
        tbody.innerHTML = '';
        users.forEach(u => {
            const devicesHtml = (u.devices || "").split(',').filter(d => d).map(d => {
                const name = systemDevicesMap[d] || d;
                return `<span class="tag ${d !== 'cloud' ? 'edge' : ''}">${name.split('(')[0]}</span>`;
            }).join('');
            const openwebuiId = u.openwebui_user_id
                ? `<code>${u.openwebui_user_id}</code>`
                : '<span style="color:#666">未绑定</span>';
            tbody.innerHTML += `<tr>
                <td style="font-weight:bold; color:var(--text-bright);">${u.username}</td>
                <td>${u.role === 'admin' ? '🛡️ Admin' : '👤 User'}</td>
                <td>${openwebuiId}</td>
                <td>${devicesHtml || '<span style="color:#666">无权限</span>'}</td>
                <td>${u.username !== 'admin' ? `<span class="delete-btn" onclick="deleteUser('${u.username}')">删除</span>` : '<span style="color:#666">不可操作</span>'}</td>
            </tr>`;
        });
    } catch (error) { tbody.innerHTML = `<tr><td colspan="5" style="color:red">加载失败: ${error.message}</td></tr>`; }
}

// 👇 1. 新增这个函数，用于控制下拉框切换时的页面效果
function toggleDeviceSelection() {
    const role = document.getElementById("new-role").value;
    const checkboxArea = document.getElementById("device-selection-area");
    const adminMsg = document.getElementById("admin-device-msg");

    if (role === 'admin') {
        checkboxArea.style.display = 'none'; // 隐藏复选框
        adminMsg.style.display = 'block';    // 显示提示语
    } else {
        checkboxArea.style.display = 'block'; // 显示复选框
        adminMsg.style.display = 'none';      // 隐藏提示语
    }
}

// 👇 2. 替换原有的 createNewUser 函数
async function createNewUser() {
    const username = document.getElementById("new-username").value;
    const password = document.getElementById("new-password").value;
    const openwebuiUserId = document.getElementById("new-openwebui-user-id").value.trim();
    const role = document.getElementById("new-role").value;
    const msgDiv = document.getElementById("add-user-msg");

    if (!username || !password) return msgDiv.innerHTML = '<span style="color:red">账号和密码必填</span>';

    let checkedDevices = "";

    // 只有在创建"普通用户"时，前端才去校验复选框
    if (role === 'user') {
        const checkedBoxes = Array.from(document.querySelectorAll('.device-check:checked'));
        checkedDevices = checkedBoxes.map(cb => cb.value).join(',');

        if (!checkedDevices) return msgDiv.innerHTML = '<span style="color:red">至少分配一台设备</span>';

        let cloudCount = 0;
        let edgeCount = 0;

        checkedBoxes.forEach(cb => {
            if (cb.getAttribute('data-type') === 'cloud') cloudCount++;
            if (cb.getAttribute('data-type') === 'edge') edgeCount++;
        });

        if (cloudCount !== 1 || edgeCount !== 1) {
            return msgDiv.innerHTML = '<span style="color:red">普通用户必须且只能分配 1个云端 和 1个边端设备</span>';
        }
    } else {
        // 如果是管理员，前端随便传个占位符，因为后端会自动查全量设备去覆盖它
        checkedDevices = "all_devices_assigned_by_backend";
    }

    msgDiv.innerHTML = '创建中...';
    try {
        await fetchWithAuth(`${API_BASE_URL}/users`, {
            method: 'POST',
            body: JSON.stringify({
                username,
                password,
                role,
                allowed_devices: checkedDevices,
                openwebui_user_id: openwebuiUserId || null,
            })
        });
        msgDiv.innerHTML = '<span style="color:var(--accent-green)">✅ 创建成功！</span>';
        document.getElementById("new-username").value = "";
        document.getElementById("new-password").value = "";
        document.getElementById("new-openwebui-user-id").value = "";
        fetchUsers();
    } catch (error) {
        msgDiv.innerHTML = `<span style="color:red">❌ ${error.message}</span>`;
    }
}

async function deleteUser(username) {
    if (!confirm(`警告：确定要永久删除账号【${username}】吗？`)) return;
    try { await fetchWithAuth(`${API_BASE_URL}/users/${username}`, { method: 'DELETE' }); fetchUsers(); } catch (error) { alert("删除失败: " + error.message); }
}

// ==========================================
// 4. 大屏设备控制与侧边栏路由
// ==========================================
window.ipToDeviceMap = {}; // 🌟 新增：用于全局缓存 IP 到设备名称的映射字典

async function loadMyDevices() {
    const selector = document.getElementById("custom-device-selector");
    selector.innerHTML = '<option value="">加载设备列表中...</option>';
    try {
        const response = await fetchWithAuth(`${API_BASE_URL}/users/my_devices`);
        const data = await response.json();
        selector.innerHTML = '';
        window.ipToDeviceMap = {};
        window.boundDeviceMap = { edge: null, cloud: null };
        data.devices.forEach(device => {
            const option = document.createElement("option");
            option.value = device.value;
            option.textContent = device.name;
            selector.appendChild(option);

            if (device.type === 'edge') {
                window.boundDeviceMap.edge = device.id;
            }
            if (device.type === 'cloud') {
                window.boundDeviceMap.cloud = device.id;
            }

            // 1. 核心逻辑：自动从 value 中提取真实 IP (现在会自动提取出 10.144.144.2)
            const ipMatch = device.value.match(/(?:\d{1,3}\.){3}\d{1,3}/);
            if (ipMatch) {
                window.ipToDeviceMap[ipMatch[0]] = device.name;
            }

            // 2. 兼容逻辑：仅保留针对本地测试的环回地址兼容，去掉硬编码的物理 IP
            if (device.id === 'cloud' || device.value.includes('127.0.0.1') || device.value.includes('localhost')) {
                window.ipToDeviceMap['127.0.0.1'] = device.name;
                window.ipToDeviceMap['localhost'] = device.name;
            }
        });
        switchGrafanaDevice();
    } catch (error) { selector.innerHTML = '<option value="">🚨 获取设备失败</option>'; }
}

function switchGrafanaDevice() {
    const val = document.getElementById("custom-device-selector").value;
    if (!val) return;
    document.getElementById("grafana-frame").src = `http://10.144.144.2:3000/d/ad9hqhg/b9a97b3?orgId=1&from=now-6h&to=now&timezone=browser&refresh=auto&kiosk&var-device=${encodeURIComponent(val)}`;
}

function switchView(viewId, navElement) {
    document.querySelectorAll('.view-section').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
    document.getElementById(viewId).classList.add('active');
    navElement.classList.add('active');
}

function initializeDashboard() {
    document.getElementById("login-overlay").style.display = "none";
    document.getElementById("login-error").textContent = "";

    const username = localStorage.getItem('current_user');
    const role = localStorage.getItem('user_role');
    document.getElementById("current-user-display").textContent = `👤 在线身份: ${username.toUpperCase()}`;

    if (role === 'admin') {
        document.getElementById("admin-panel-btn").style.display = "inline-block";
    } else {
        document.getElementById("admin-panel-btn").style.display = "none";
    }

    loadMyDevices();
    // 👇 新增：启动模型状态流
    startSandboxModelStream();
}

// ==========================================
// 6. 全局初始化触发器
// ==========================================
if (localStorage.getItem('jwt_token')) {
    initializeDashboard();
}
