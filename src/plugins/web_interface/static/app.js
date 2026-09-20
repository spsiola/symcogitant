document.addEventListener('DOMContentLoaded', () => {
    const tabsNav = document.getElementById('tabs-nav');
    const tabsContentContainer = document.getElementById('tabs-content-container');
    const ws = new WebSocket(`ws://${window.location.host}/ws/logs`);
    
    // state mapping: source -> { btn, content, logContainer, metricsContainer, unreadCount }
    const tabs = {};
    let activeTabId = null;
    let globalLlmKeys = {};

    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        const source = data.source || 'harness core';
        
        ensureTabExists(source);
        
        if (data.type === 'metric') {
            ensureTabExists(source);
            updateMetrics(source, data);
            if (data.data && data.data.llm_keys) {
                globalLlmKeys = data.data.llm_keys;
            }
            if (data.data && data.data.llm_models) {
                updateModelsDropdown(data.data.llm_models);
            }
        } else if (data.type === 'init_data') {
            if (data.data && data.data.llm_keys) {
                globalLlmKeys = data.data.llm_keys;
            }
            if (data.data && data.data.llm_models) {
                ensureTabExists('LLMChatPlugin');
                updateModelsDropdown(data.data.llm_models);
            }
        } else if (data.type === 'log') {
            appendLog(source, data);
        } else if (data.type === 'ui_chat_output') {
            appendChatMessage(source, data);
        } else if (data.type === 'ui_chat_system') {
            appendChatSystemMessage(source, data);
        } else if (data.type === 'ui_chat_clear') {
            if (tabs[source] && tabs[source].chatMessages) {
                tabs[source].chatMessages.innerHTML = '';
                tabs[source].messages_array = [];
            }
        } else {
            // Render generic system events as raw JSON blocks
            appendRawEvent(source, data);
        }
        
        // update badge if not active
        if (activeTabId !== source) {
            tabs[source].unreadCount++;
            updateBadge(source);
        }
    };

    ws.onclose = () => {
        const source = 'harness core';
        ensureTabExists(source);
        appendLog(source, { level: 'WARNING', message: 'Connection lost. Please refresh.' });
    };

    function ensureTabExists(id) {
        if (tabs[id]) return;

        // Create Tab Button
        const btn = document.createElement('button');
        btn.className = 'tab-btn';
        btn.innerHTML = `<span>${id}</span> <span class="tab-badge" style="display:none">0</span>`;
        btn.onclick = () => switchTab(id);
        
        // Create Content Panel
        const content = document.createElement('div');
        content.className = 'tab-content';
        
        const metricsContainer = document.createElement('div');
        metricsContainer.className = 'metrics-container';
        metricsContainer.style.display = 'none'; // hidden by default until metrics arrive
        
        const logContainer = document.createElement('div');
        logContainer.className = 'log-container';
        
        content.appendChild(metricsContainer);
        content.appendChild(logContainer);
        
        
        tabsNav.appendChild(btn);
        tabsContentContainer.appendChild(content);
        
        tabs[id] = {
            btn: btn,
            content: content,
            logContainer: logContainer,
            metricsContainer: metricsContainer,
            unreadCount: 0,
            messages_array: []
        };

        if (id === 'LLMChatPlugin') {
            // Transform logContainer into chat UI
            content.classList.add('chat-tab');
            logContainer.classList.add('chat-mode');
            
            const chatHeaderArea = document.createElement('div');
            chatHeaderArea.className = 'chat-header-area';
            
            const chatHeaderLeft = document.createElement('div');
            chatHeaderLeft.className = 'chat-header-left';
            
            const chatModelSelect = document.createElement('select');
            chatModelSelect.className = 'chat-model-select';
            tabs[id].chatModelSelect = chatModelSelect;
            
            const chatKeySelect = document.createElement('select');
            chatKeySelect.className = 'chat-key-select';
            const defaultKeyOpt = document.createElement('option');
            defaultKeyOpt.value = 'default';
            defaultKeyOpt.textContent = 'Default Key';
            chatKeySelect.appendChild(defaultKeyOpt);
            chatKeySelect.disabled = true;
            tabs[id].chatKeySelect = chatKeySelect;
            
            chatModelSelect.addEventListener('change', () => {
                updateKeyDropdown();
            });

            const contextSizeBadge = document.createElement('div');
            const defaultOpt = document.createElement('option');
            defaultOpt.value = 'default';
            defaultOpt.textContent = 'Default Model';
            chatModelSelect.appendChild(defaultOpt);
            tabs[id].chatModelSelect = chatModelSelect;
            
            contextSizeBadge.className = 'context-size-badge';
            contextSizeBadge.textContent = 'Контекст: 0 симв';
            tabs[id].contextSizeBadge = contextSizeBadge;
            
            const chatCtxInput = document.createElement('input');
            chatCtxInput.type = 'number';
            chatCtxInput.className = 'chat-ctx-input';
            chatCtxInput.placeholder = 'Context Size (e.g. 8192)';
            chatCtxInput.title = 'Ограничение контекста (num_ctx). Оставьте пустым для авто.';
            tabs[id].chatCtxInput = chatCtxInput;
            
            chatHeaderLeft.appendChild(chatModelSelect);
            chatHeaderLeft.appendChild(chatKeySelect);
            chatHeaderLeft.appendChild(chatCtxInput);
            chatHeaderLeft.appendChild(contextSizeBadge);
            
            const chatHeaderButtons = document.createElement('div');
            chatHeaderButtons.className = 'chat-header-buttons';
            
            const btnClear = document.createElement('button');
            btnClear.className = 'chat-btn btn-clear';
            btnClear.textContent = 'Очистить контекст';
            btnClear.onclick = () => {
                openConfirmModal('Вы уверены, что хотите очистить контекст? Текущая история будет архивирована.', () => {
                    ws.send(JSON.stringify({type: 'ui_chat_command', target: id, command: 'clear_context'}));
                });
            };
            
            const btnEdit = document.createElement('button');
            btnEdit.className = 'chat-btn btn-edit';
            btnEdit.textContent = 'Редактировать контекст';
            btnEdit.onclick = () => openEditModal(id);
            
            const btnCopy = document.createElement('button');
            btnCopy.className = 'chat-btn btn-copy';
            btnCopy.textContent = 'Копировать';
            btnCopy.onclick = () => {
                const textToCopy = JSON.stringify(tabs[id].messages_array, null, 2);
                navigator.clipboard.writeText(textToCopy).then(() => {
                    const origText = btnCopy.textContent;
                    btnCopy.textContent = 'Скопировано!';
                    setTimeout(() => btnCopy.textContent = origText, 2000);
                });
            };
            
            const btnCopyAll = document.createElement('button');
            btnCopyAll.className = 'chat-btn btn-copy-all';
            btnCopyAll.textContent = 'Копировать все';
            btnCopyAll.onclick = () => {
                const textToCopy = tabs[id].chatMessages.innerText;
                navigator.clipboard.writeText(textToCopy).then(() => {
                    const origText = btnCopyAll.textContent;
                    btnCopyAll.textContent = 'Скопировано!';
                    setTimeout(() => btnCopyAll.textContent = origText, 2000);
                });
            };
            
            chatHeaderButtons.appendChild(btnClear);
            chatHeaderButtons.appendChild(btnEdit);
            chatHeaderButtons.appendChild(btnCopy);
            chatHeaderButtons.appendChild(btnCopyAll);
            
            chatHeaderArea.appendChild(chatHeaderLeft);
            chatHeaderArea.appendChild(chatHeaderButtons);
            
            logContainer.appendChild(chatHeaderArea);
            
            const chatMessages = document.createElement('div');
            chatMessages.className = 'chat-messages';
            logContainer.appendChild(chatMessages);
            tabs[id].chatMessages = chatMessages;
            
            const chatInputArea = document.createElement('div');
            chatInputArea.className = 'chat-input-area';
            
            const chatInputWrapper = document.createElement('div');
            chatInputWrapper.className = 'chat-input-wrapper';
            
            const chatInput = document.createElement('input');
            chatInput.type = 'text';
            chatInput.placeholder = 'Type your message...';
            chatInput.className = 'chat-input';
            
            const chatCounter = document.createElement('div');
            chatCounter.className = 'chat-counter';
            chatCounter.textContent = '0 символов | ~0 токенов';
            
            chatInput.addEventListener('input', () => {
                const len = chatInput.value.length;
                const approxTokens = Math.ceil(len / 3);
                chatCounter.textContent = `${len} символов | ~${approxTokens} токенов`;
            });
            
            chatInputWrapper.appendChild(chatInput);
            chatInputWrapper.appendChild(chatCounter);
            
            const chatSendBtn = document.createElement('button');
            chatSendBtn.textContent = 'Send';
            chatSendBtn.className = 'chat-send-btn';
            
            const sendMessage = () => {
                const text = chatInput.value.trim();
                if (text) {
                    tabs[id].messages_array.push({role: 'user', content: text});
                    
                    const selectedModel = tabs[id].chatModelSelect ? tabs[id].chatModelSelect.value : null;
                    const ctxVal = tabs[id].chatCtxInput ? tabs[id].chatCtxInput.value : null;
                    const keyName = tabs[id].chatKeySelect && !tabs[id].chatKeySelect.disabled ? tabs[id].chatKeySelect.value : null;
                    
                    ws.send(JSON.stringify({
                        type: 'ui_chat_input',
                        target: id,
                        message: text,
                        model: selectedModel !== 'default' ? selectedModel : undefined,
                        num_ctx: ctxVal ? parseInt(ctxVal, 10) : undefined,
                        api_key_name: keyName
                    }));
                    
                    appendChatMessage(id, {
                        role: 'user',
                        content: text
                    });
                    
                    chatInput.value = '';
                    chatCounter.textContent = '0 символов | ~0 токенов';
                }
            };
            
            chatSendBtn.onclick = sendMessage;
            chatInput.addEventListener('keypress', (e) => {
                if (e.key === 'Enter') sendMessage();
            });
            
            chatInputArea.appendChild(chatInputWrapper);
            chatInputArea.appendChild(chatSendBtn);
            
            content.appendChild(chatInputArea);
            // Hide the default log container since we use chat UI instead
            logContainer.style.flex = '1';
        }

        if (activeTabId === null) {
            switchTab(id);
        }
    }

    function switchTab(id) {
        if (activeTabId && tabs[activeTabId]) {
            tabs[activeTabId].btn.classList.remove('active');
            tabs[activeTabId].content.classList.remove('active');
        }
        
        activeTabId = id;
        tabs[id].btn.classList.add('active');
        tabs[id].content.classList.add('active');
        tabs[id].unreadCount = 0;
        updateBadge(id);
        
        // scroll to bottom
        tabs[id].content.scrollTop = tabs[id].content.scrollHeight;
    }

    function updateBadge(id) {
        const badge = tabs[id].btn.querySelector('.tab-badge');
        if (tabs[id].unreadCount > 0) {
            badge.textContent = tabs[id].unreadCount > 99 ? '99+' : tabs[id].unreadCount;
            badge.style.display = 'inline-block';
        } else {
            badge.style.display = 'none';
        }
    }

    function appendLog(id, data) {
        const logContainer = tabs[id].logContainer;
        const entry = document.createElement('div');
        const levelClass = data.level ? data.level.toLowerCase() : 'info';
        entry.className = `log-entry ${levelClass}`;
        
        const time = new Date().toLocaleTimeString();
        const msg = data.message || '';
        
        entry.innerHTML = `
            <span style="color: #64748b">[${time}]</span>
            <span class="log-source">[${id}]</span>
            <span>${msg}</span>
        `;
        
        logContainer.appendChild(entry);
        
        if (activeTabId === id) {
            tabs[id].content.scrollTop = tabs[id].content.scrollHeight;
        }
    }

    function appendRawEvent(id, data) {
        const logContainer = tabs[id].logContainer;
        const entry = document.createElement('div');
        entry.className = `log-entry debug`;
        
        const time = new Date().toLocaleTimeString();
        
        entry.innerHTML = `
            <span style="color: #64748b">[${time}]</span>
            <span class="log-source">[${id}]</span>
            <span style="color: #818cf8">[RAW EVENT]</span>
            <pre style="margin: 5px 0 5px 0; padding: 10px; background: rgba(0,0,0,0.3); border-radius: 6px; font-family: monospace; white-space: pre-wrap; word-wrap: break-word; font-size: 0.85rem; color: #a5b4fc; border: 1px solid rgba(165, 180, 252, 0.2);">${JSON.stringify(data, null, 2)}</pre>
        `;
        
        logContainer.appendChild(entry);
        
        if (activeTabId === id) {
            tabs[id].content.scrollTop = tabs[id].content.scrollHeight;
        }
    }

    function updateMetrics(id, payload) {
        const metricsContainer = tabs[id].metricsContainer;
        metricsContainer.style.display = 'flex'; // reveal metrics
        
        const data = payload.data;
        const timeStr = new Date(payload.timestamp * 1000).toLocaleTimeString();

        let html = '';
        
        if (data.cpu) {
            html += `
                <div class="metric-card">
                    <div class="metric-header">CPU Usage</div>
                    <div class="metric-body">${data.cpu.usage_percent}% (${data.cpu.cores} Cores)</div>
                    <div class="metric-updated">Updated: ${timeStr}</div>
                </div>
            `;
        }

        if (data.ram) {
            html += `
                <div class="metric-card">
                    <div class="metric-header">RAM Usage</div>
                    <div class="metric-body">${data.ram.usage_percent}%</div>
                    <div style="font-size: 0.8rem; color: var(--text-secondary); margin-top: 5px;">
                        ${data.ram.used_gb} / ${data.ram.total_gb} GB
                    </div>
                    <div class="metric-updated">Updated: ${timeStr}</div>
                </div>
            `;
        }

        if (data.llms) {
            let llmsHtml = '';
            for (const [name, status] of Object.entries(data.llms)) {
                let displayName = name;
                if (globalLlmKeys && globalLlmKeys[name] && globalLlmKeys[name].length > 1) {
                    displayName = `[${globalLlmKeys[name].length}] ${name}`;
                }
                
                llmsHtml += `
                    <div class="llm-status">
                        <span>${displayName}</span>
                        <span class="status-lamp ${status}"></span>
                    </div>
                `;
            }
            
            html += `
                <div class="metric-card" style="flex: 2;">
                    <div class="metric-header">LLM Endpoints</div>
                    <div class="metric-body" style="font-weight: 400; font-family: 'Inter', sans-serif;">
                        ${llmsHtml}
                    </div>
                    <div class="metric-updated">Updated: ${timeStr}</div>
                </div>
            `;
        }

        metricsContainer.innerHTML = html;
        
        // Also log to history
        appendLog(id, {
            level: 'INFO',
            message: `[Metrics Updated] CPU: ${data.cpu ? data.cpu.usage_percent : '?'}%, RAM: ${data.ram ? data.ram.usage_percent : '?'}%`
        });
    }

    function updateModelsDropdown(llm_models) {
        if (llm_models && tabs['LLMChatPlugin'] && tabs['LLMChatPlugin'].chatModelSelect) {
            const select = tabs['LLMChatPlugin'].chatModelSelect;
            const currentVal = select.value;
            select.innerHTML = '';
            
            const localGroup = document.createElement('optgroup');
            localGroup.label = 'Local Models';
            const cloudGroup = document.createElement('optgroup');
            cloudGroup.label = 'Cloud Models';
            
            if (llm_models.local && Array.isArray(llm_models.local)) {
                llm_models.local.forEach(model => {
                    const opt = document.createElement('option');
                    opt.value = model;
                    let displayName = model;
                    const provider = model.includes('/') ? model.split('/')[0].toLowerCase() : null;
                    if (provider && globalLlmKeys && globalLlmKeys[provider] && globalLlmKeys[provider].length > 1) {
                        displayName = `[${globalLlmKeys[provider].length}] ${model}`;
                    }
                    opt.textContent = displayName;
                    localGroup.appendChild(opt);
                });
            }

            if (llm_models.cloud && Array.isArray(llm_models.cloud)) {
                llm_models.cloud.forEach(model => {
                    const opt = document.createElement('option');
                    opt.value = model;
                    let displayName = model;
                    const provider = model.includes('/') ? model.split('/')[0].toLowerCase() : null;
                    if (provider && globalLlmKeys && globalLlmKeys[provider] && globalLlmKeys[provider].length > 1) {
                        displayName = `[${globalLlmKeys[provider].length}] ${model}`;
                    }
                    opt.textContent = displayName;
                    cloudGroup.appendChild(opt);
                });
            }
            
            if (localGroup.children.length > 0) select.appendChild(localGroup);
            if (cloudGroup.children.length > 0) select.appendChild(cloudGroup);
            
            if (select.children.length === 0) {
                const defaultOpt = document.createElement('option');
                defaultOpt.value = 'default';
                defaultOpt.textContent = 'Default Model';
                select.appendChild(defaultOpt);
            }
            
            // Restore previous selection if it still exists
            if (Array.from(select.options).some(o => o.value === currentVal)) {
                select.value = currentVal;
            }
            updateKeyDropdown();
        }
    }

    function updateKeyDropdown() {
        if (!tabs['LLMChatPlugin'] || !tabs['LLMChatPlugin'].chatModelSelect || !tabs['LLMChatPlugin'].chatKeySelect) return;
        const modelSelect = tabs['LLMChatPlugin'].chatModelSelect;
        const keySelect = tabs['LLMChatPlugin'].chatKeySelect;
        
        const selectedModel = modelSelect.value;
        const provider = selectedModel.includes('/') ? selectedModel.split('/')[0].toLowerCase() : null;
        
        const keysForProvider = provider && globalLlmKeys[provider] ? globalLlmKeys[provider] : [];
        
        keySelect.innerHTML = '';
        if (keysForProvider.length > 1) {
            keysForProvider.forEach(keyName => {
                const opt = document.createElement('option');
                opt.value = keyName;
                opt.textContent = keyName === 'default' ? 'Default Key' : keyName;
                keySelect.appendChild(opt);
            });
            keySelect.disabled = false;
        } else {
            const opt = document.createElement('option');
            opt.value = keysForProvider.length === 1 ? keysForProvider[0] : 'default';
            opt.textContent = keysForProvider.length === 1 && keysForProvider[0] !== 'default' ? keysForProvider[0] : 'Default Key';
            keySelect.appendChild(opt);
            keySelect.disabled = true;
        }
    }

    function appendChatMessage(id, data) {
        if (!tabs[id].chatMessages) return;
        
        const container = tabs[id].chatMessages;
        
        // Add to array if it is not already there (user messages are added eagerly, system/assistant are from server)
        // Note: when loading history, all messages come from server, so we must add them to array
        // We avoid duplicating user messages by checking if the last message in array is identical user message (simple heuristic)
        const arr = tabs[id].messages_array;
        
        const newMsg = {role: data.role, content: data.content};
        if (data.metadata) {
            newMsg.metadata = data.metadata;
        }
        
        if (data.role !== 'user' || arr.length === 0 || arr[arr.length - 1].content !== data.content || arr[arr.length - 1].role !== 'user') {
            arr.push(newMsg);
        }
        
        // Update total context size badge
        if (tabs[id].contextSizeBadge) {
            const totalChars = arr.reduce((sum, msg) => sum + (msg.content || '').length, 0);
            tabs[id].contextSizeBadge.textContent = `Контекст: ${totalChars} симв`;
        }
        
        const entry = document.createElement('div');
        
        // role can be 'user' or 'assistant'
        const role = data.role || 'assistant';
        entry.className = `chat-bubble chat-${role}`;
        
        let html = `<div class="chat-content">${data.content || ''}</div>`;
        if (data.metadata) {
            let ts = '';
            if (data.metadata.timestamp) {
                const date = new Date(data.metadata.timestamp);
                if (!isNaN(date)) {
                    ts = ' • ' + date.toLocaleTimeString();
                }
            }
            const modelStr = data.metadata.model ? data.metadata.model : '';
            const latStr = data.metadata.latency_sec ? ` • ${data.metadata.latency_sec.toFixed(2)}s` : '';
            html += `<div class="chat-bubble-meta">${modelStr}${latStr}${ts}</div>`;
        }
        entry.innerHTML = html;
        container.appendChild(entry);
        
        if (activeTabId === id) {
            container.scrollTop = container.scrollHeight;
            tabs[id].content.scrollTop = tabs[id].content.scrollHeight;
        }
    }

    function appendChatSystemMessage(id, data) {
        if (!tabs[id].chatMessages) return;
        
        // Also update context size badge if messages array was just cleared
        if (tabs[id].messages_array && tabs[id].messages_array.length === 0 && tabs[id].contextSizeBadge) {
            tabs[id].contextSizeBadge.textContent = `Контекст: 0 симв`;
        }
        
        const container = tabs[id].chatMessages;
        const entry = document.createElement('div');
        entry.className = `chat-system-message`;
        entry.innerHTML = `<span>⚙️ ${data.message || ''}</span>`;
        container.appendChild(entry);
        
        if (activeTabId === id) {
            container.scrollTop = container.scrollHeight;
            tabs[id].content.scrollTop = tabs[id].content.scrollHeight;
        }
    }

    function openEditModal(id) {
        const modal = document.createElement('div');
        modal.className = 'edit-modal';
        
        const modalContent = document.createElement('div');
        modalContent.className = 'edit-modal-content';
        
        const title = document.createElement('h3');
        title.textContent = 'Редактировать контекст JSON';
        title.style.marginTop = '0';
        
        const textarea = document.createElement('textarea');
        textarea.className = 'edit-modal-textarea';
        textarea.value = JSON.stringify(tabs[id].messages_array, null, 2);
        
        const btnContainer = document.createElement('div');
        btnContainer.className = 'edit-modal-buttons';
        
        const btnSave = document.createElement('button');
        btnSave.className = 'chat-btn';
        btnSave.textContent = 'Сохранить';
        btnSave.onclick = () => {
            try {
                const newMessages = JSON.parse(textarea.value);
                ws.send(JSON.stringify({
                    type: 'ui_chat_command',
                    target: id,
                    command: 'sync_context',
                    messages: newMessages
                }));
                document.body.removeChild(modal);
            } catch (e) {
                alert('Ошибка JSON: ' + e.message);
            }
        };
        
        const btnCancel = document.createElement('button');
        btnCancel.className = 'chat-btn btn-cancel';
        btnCancel.textContent = 'Отмена';
        btnCancel.onclick = () => document.body.removeChild(modal);
        
        btnContainer.appendChild(btnCancel);
        btnContainer.appendChild(btnSave);
        
        modalContent.appendChild(title);
        modalContent.appendChild(textarea);
        modalContent.appendChild(btnContainer);
        
        modal.appendChild(modalContent);
        document.body.appendChild(modal);
    }

    function openConfirmModal(message, onConfirm) {
        const modal = document.createElement('div');
        modal.className = 'edit-modal confirm-modal';
        
        const modalContent = document.createElement('div');
        modalContent.className = 'edit-modal-content';
        modalContent.style.maxWidth = '400px';
        
        const title = document.createElement('h3');
        title.textContent = 'Подтверждение';
        title.style.marginTop = '0';
        title.style.color = '#f87171';
        
        const text = document.createElement('p');
        text.textContent = message;
        text.style.fontSize = '0.95rem';
        text.style.lineHeight = '1.4';
        
        const btnContainer = document.createElement('div');
        btnContainer.className = 'edit-modal-buttons';
        
        const btnYes = document.createElement('button');
        btnYes.className = 'chat-btn btn-clear';
        btnYes.textContent = 'Да, уверен';
        btnYes.onclick = () => {
            onConfirm();
            document.body.removeChild(modal);
        };
        
        const btnNo = document.createElement('button');
        btnNo.className = 'chat-btn btn-cancel';
        btnNo.textContent = 'Отмена';
        btnNo.onclick = () => document.body.removeChild(modal);
        
        btnContainer.appendChild(btnNo);
        btnContainer.appendChild(btnYes);
        
        modalContent.appendChild(title);
        modalContent.appendChild(text);
        modalContent.appendChild(btnContainer);
        
        modal.appendChild(modalContent);
        document.body.appendChild(modal);
    }
});
