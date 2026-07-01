document.addEventListener('DOMContentLoaded', () => {
    const dropZone = document.getElementById('dropZone');
    const fileInput = document.getElementById('fileInput');
    const statusText = document.getElementById('uploadStatus');
    const logContainer = document.getElementById('logContainer');
    const toggleLogsBtn = document.getElementById('toggleLogs');
    const searchInput = document.getElementById('searchInput');
    const sendBtn = document.getElementById('searchBtn');
    const chatHistory = document.getElementById('chatHistory');

    // --- Sidebar Toggle (Responsive) ---
    const sidebarToggle = document.getElementById('sidebarToggle');
    const sidebar = document.querySelector('.sidebar');
    const sidebarOverlay = document.getElementById('sidebarOverlay');

    function openSidebar() {
        sidebar.classList.add('active');
        sidebarOverlay.classList.add('active');
        document.body.style.overflow = 'hidden'; // Prevent background scroll
    }

    function closeSidebar() {
        sidebar.classList.remove('active');
        sidebarOverlay.classList.remove('active');
        document.body.style.overflow = '';
    }

    if (sidebarToggle) {
        sidebarToggle.addEventListener('click', () => {
            if (sidebar.classList.contains('active')) {
                closeSidebar();
            } else {
                openSidebar();
            }
        });
    }

    if (sidebarOverlay) {
        sidebarOverlay.addEventListener('click', closeSidebar);
    }

    // Close sidebar on escape key
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            closeSidebar();
            closeVideoPlayerModal();
        }
    });

    // --- Theme Toggle ---
    const themeToggleBtn = document.getElementById('themeToggle');
    const themeIcon = themeToggleBtn ? themeToggleBtn.querySelector('i') : null;

    function setTheme(theme) {
        document.documentElement.setAttribute('data-theme', theme);
        localStorage.setItem('theme', theme);
        
        if (themeIcon) {
            if (theme === 'light') {
                themeIcon.classList.replace('fa-sun', 'fa-moon');
            } else {
                themeIcon.classList.replace('fa-moon', 'fa-sun');
            }
        }
    }

    // Initialize theme
    const savedTheme = localStorage.getItem('theme') || 'dark';
    setTheme(savedTheme);

    if (themeToggleBtn) {
        themeToggleBtn.addEventListener('click', () => {
            const currentTheme = document.documentElement.getAttribute('data-theme') || 'dark';
            const newTheme = currentTheme === 'light' ? 'dark' : 'light';
            setTheme(newTheme);
        });
    }

    // --- Configuration Management (A/B Testing) ---
    // Configurations are loaded from the backend API for persistence
    let configurations = {};  // Will be populated from API
    let currentConfigId = 'c1';
    const configSelect = document.getElementById('configSelect');
    const configSummary = document.getElementById('configSummary');
    const editConfigBtn = document.getElementById('editConfigBtn');
    const addConfigBtn = document.getElementById('addConfigBtn');

    // Load configurations from backend API
    async function loadConfigurations() {
        try {
            const response = await fetch('/configs');
            if (response.ok) {
                const data = await response.json();
                configurations = {};
                data.configurations.forEach(config => {
                    configurations[config.id] = config;
                });
                addLog(`Loaded ${data.configurations.length} configurations from server.`);
                
                // Set current config to first available if current doesn't exist
                if (!configurations[currentConfigId] && Object.keys(configurations).length > 0) {
                    currentConfigId = Object.keys(configurations)[0];
                }
                
                populateConfigDropdown();
            } else {
                addLog('Failed to load configurations from server, using defaults.');
                useDefaultConfigurations();
            }
        } catch (error) {
            console.error('Error loading configurations:', error);
            addLog('Error loading configurations, using defaults.');
            useDefaultConfigurations();
        }
    }

    function useDefaultConfigurations() {
        configurations = {
            'c1': {
                id: 'c1',
                name: 'BLIP + Whisper Base (Fast)',
                vision_model: 'Salesforce/blip-image-captioning-base',
                speech_model: 'base',
                enable_vision: true,
                frame_interval: 5
            },
            'c2': {
                id: 'c2',
                name: 'BLIP + Whisper Large',
                vision_model: 'Salesforce/blip-image-captioning-base',
                speech_model: 'large-v3',
                enable_vision: true,
                frame_interval: 5
            },
            'c3': {
                id: 'c3',
                name: 'Florence-2 + Distil-Whisper',
                vision_model: 'microsoft/Florence-2-large',
                speech_model: 'distil-large-v3',
                enable_vision: true,
                frame_interval: 5
            },
            'c4': {
                id: 'c4',
                name: 'Qwen VL + Whisper Base',
                vision_model: 'Qwen/Qwen2.5-VL-7B-Instruct',
                speech_model: 'base',
                enable_vision: true,
                frame_interval: 5
            }
        };
        populateConfigDropdown();
    }

    function populateConfigDropdown() {
        if (!configSelect) return;
        
        configSelect.innerHTML = '';
        Object.values(configurations).forEach(config => {
            const option = document.createElement('option');
            option.value = config.id;
            option.textContent = config.name;
            if (config.id === currentConfigId) option.selected = true;
            configSelect.appendChild(option);
        });
        
        updateConfigSummary();
    }

    function initConfigUI() {
        if (!configSelect) return;

        // Load configurations from API first
        loadConfigurations();

        // Event Listeners
        configSelect.addEventListener('change', (e) => {
            currentConfigId = e.target.value;
            updateConfigSummary();
            addLog(`Switched to ${configurations[currentConfigId].name}`);
            
            // Refresh data list to simulate context switch (reset to page 1)
            currentPage = 0;
            fetchIngestedData('all', true); 
        });

        if (editConfigBtn) {
            editConfigBtn.addEventListener('click', () => {
                openSettingsModal();
            });
        }

        if (addConfigBtn) {
            addConfigBtn.addEventListener('click', () => {
                openNewConfigModal();
            });
        }
    }

    // Create New Configuration Modal
    function openNewConfigModal() {
        const name = prompt("Enter a name for the new configuration:");
        if (!name || name.trim() === '') return;
        
        // Generate a unique ID
        const id = 'c' + Date.now();
        
        // Create with current config as template
        const template = configurations[currentConfigId] || Object.values(configurations)[0];
        
        const newConfig = {
            id: id,
            name: name.trim(),
            vision_model: template.vision_model,
            speech_model: template.speech_model,
            enable_vision: template.enable_vision,
            frame_interval: template.frame_interval || 5
        };
        
        // Save to backend
        fetch('/configs', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(newConfig)
        })
        .then(response => {
            if (response.ok) {
                return response.json();
            }
            throw new Error('Failed to create configuration');
        })
        .then(savedConfig => {
            configurations[savedConfig.id] = savedConfig;
            currentConfigId = savedConfig.id;
            populateConfigDropdown();
            addLog(`Created new configuration: ${savedConfig.name}`);
            
            // Open settings modal to customize the new config
            openSettingsModal();
        })
        .catch(error => {
            console.error('Error creating configuration:', error);
            addLog(`Error creating configuration: ${error.message}`);
        });
    }

    function updateConfigSummary() {
        if (!configSummary) return;
        const config = configurations[currentConfigId];
        if (config) {
            configSummary.textContent = `${config.vision_model} • ${config.speech_model}`;
        }
    }

    initConfigUI();

    // --- Logging System ---
    function addLog(message) {
        const entry = document.createElement('div');
        entry.className = 'log-entry';
        
        const now = new Date();
        const timeString = now.toLocaleTimeString('en-US', { hour12: false });
        
        entry.innerHTML = `<span class="timestamp">[${timeString}]</span> ${message}`;
        logContainer.appendChild(entry);
        logContainer.scrollTop = logContainer.scrollHeight;
    }

    addLog('System initialized.');
    addLog('Ready for video ingestion.');

    // --- Log Toggle ---
    if (toggleLogsBtn) {
        toggleLogsBtn.addEventListener('click', () => {
            logContainer.classList.toggle('minimized');
            const icon = toggleLogsBtn.querySelector('i');
            if (logContainer.classList.contains('minimized')) {
                icon.classList.replace('fa-chevron-down', 'fa-chevron-up');
            } else {
                icon.classList.replace('fa-chevron-up', 'fa-chevron-down');
            }
        });
    }

    // --- Past Data Tabs ---
    const dataTabs = document.querySelectorAll('.data-tab');
    const dataList = document.getElementById('ingestedDataList');

    // P1 Fix: Pagination state management
    let currentPage = 0;
    const pageSize = 20;
    let totalVideoCount = 0;

    async function fetchIngestedData(view = 'all', resetPage = false) {
        try {
            // Reset pagination when switching views or configs
            if (resetPage) {
                currentPage = 0;
            }
            
            dataList.innerHTML = '<div class="data-item" style="justify-content:center; color:var(--text-muted);">Loading...</div>';
            
            // Wait for configurations to load if they haven't yet
            if (Object.keys(configurations).length === 0) {
                await loadConfigurations();
            }
            
            // --- FIXED: Send individual model parameters instead of just ?model= ---
            // This ensures the API computes the EXACT same config_hash as the Frontend config
            const config = configurations[currentConfigId];
            
            if (!config) {
                throw new Error('No configuration available');
            }
            
            // Build query params from the active configuration
            const params = new URLSearchParams();
            params.append('vision_model', config.vision_model);
            params.append('speech_model', config.speech_model);
            params.append('frame_interval', config.frame_interval || 5);
            
            // P1 Fix: Add pagination parameters
            const skip = currentPage * pageSize;
            params.append('skip', skip);
            params.append('limit', pageSize);
            
            const queryString = params.toString();
            const url = `/videos${queryString ? '?' + queryString : ''}`;
            
            console.log(`[fetchIngestedData] Fetching: ${url} (page ${currentPage + 1})`);
            console.log(`[fetchIngestedData] Frontend Config: vision=${config.vision_model}, speech=${config.speech_model}`);

            // Fetch videos filtered by model configuration
            const response = await fetch(url);
            if (!response.ok) throw new Error('Failed to fetch videos');
            
            // --- DEBUG: Log the backend's config hash from response headers ---
            const backendConfigHash = response.headers.get('X-Debug-Config-Hash');
            const backendVisionModel = response.headers.get('X-Debug-Vision-Model');
            const backendSpeechModel = response.headers.get('X-Debug-Speech-Model');
            
            // P1 Fix: Get total count from response header for pagination
            totalVideoCount = parseInt(response.headers.get('X-Total-Count')) || 0;
            
            console.log(`[fetchIngestedData] Backend Debug Headers:`);
            console.log(`  X-Debug-Config-Hash: ${backendConfigHash}`);
            console.log(`  X-Debug-Vision-Model: ${backendVisionModel}`);
            console.log(`  X-Debug-Speech-Model: ${backendSpeechModel}`);
            console.log(`  X-Total-Count: ${totalVideoCount}`);
            console.log(`  Fixed Embedding: all-MiniLM-L6-v2`);
            
            let videos = await response.json();
            
            // Log the config_hash returned by backend for debugging
            if (videos.length > 0) {
                console.log(`[fetchIngestedData] First video status: ${videos[0].status}, config_hash: ${videos[0].config_hash}`);
            }
            
            // Client-side filtering for POC
            if (view === 'recent') {
                // Sort by created_at desc (already done by backend) and take top 5
                videos = videos.slice(0, 5);
            } else if (view === 'favorites') {
                // Placeholder: Filter by some favorite flag (not yet in DB)
                videos = videos.filter(v => v.is_favorite); 
            }

            renderDataList(videos);
            
            // P1 Fix: Update pagination controls
            updatePaginationControls();
        } catch (error) {
            console.error('Error fetching data:', error);
            dataList.innerHTML = '<div class="data-item" style="justify-content:center; color:var(--error);">Failed to load data</div>';
        }
    }
    
    // P1 Fix: Pagination control functions
    function updatePaginationControls() {
        const paginationContainer = document.getElementById('paginationControls');
        if (!paginationContainer) return;
        
        const totalPages = Math.ceil(totalVideoCount / pageSize);
        const pageInfo = paginationContainer.querySelector('.page-info');
        const prevBtn = paginationContainer.querySelector('#prevPageBtn');
        const nextBtn = paginationContainer.querySelector('#nextPageBtn');
        
        if (pageInfo) {
            pageInfo.textContent = totalPages > 0 ? `Page ${currentPage + 1} of ${totalPages}` : 'No videos';
        }
        
        if (prevBtn) {
            prevBtn.disabled = currentPage === 0;
        }
        
        if (nextBtn) {
            nextBtn.disabled = currentPage >= totalPages - 1;
        }
        
        // Show/hide pagination based on total count
        paginationContainer.style.display = totalVideoCount > pageSize ? 'flex' : 'none';
    }
    
    function goToNextPage() {
        const totalPages = Math.ceil(totalVideoCount / pageSize);
        if (currentPage < totalPages - 1) {
            currentPage++;
            const activeTab = document.querySelector('.data-tab.active');
            const view = activeTab ? activeTab.getAttribute('data-view') : 'all';
            fetchIngestedData(view);
        }
    }
    
    function goToPrevPage() {
        if (currentPage > 0) {
            currentPage--;
            const activeTab = document.querySelector('.data-tab.active');
            const view = activeTab ? activeTab.getAttribute('data-view') : 'all';
            fetchIngestedData(view);
        }
    }
    
    // Initialize pagination button listeners
    document.addEventListener('DOMContentLoaded', () => {
        const prevBtn = document.getElementById('prevPageBtn');
        const nextBtn = document.getElementById('nextPageBtn');
        
        if (prevBtn) {
            prevBtn.addEventListener('click', goToPrevPage);
        }
        if (nextBtn) {
            nextBtn.addEventListener('click', goToNextPage);
        }
    });

    function renderDataList(videos) {
        dataList.innerHTML = '';
        
        if (videos.length === 0) {
            dataList.innerHTML = '<div class="data-item" style="justify-content:center; color:var(--text-muted);">No videos found</div>';
            return;
        }

        videos.forEach(video => {
            const item = document.createElement('div');
            item.className = 'data-item';
            if (selectedVideoId === video.id) {
                item.classList.add('selected');
            }
            
            let statusClass = 'processing';
            let statusText = video.status || 'Unknown';
            
            // Map backend status to UI classes and user-friendly text
            if (statusText === 'indexed') {
                statusClass = 'success';
                statusText = 'Ready';
            } else if (statusText === 'completed') {
                statusClass = 'processing';
                statusText = 'Indexing...';  // More accurate than 'Embedding...'
            } else if (statusText === 'failed') {
                statusClass = 'error';
                statusText = 'Failed';
            } else if (statusText === 'queued') {
                statusClass = 'pending';
                statusText = 'Queued';
            } else if (statusText === 'processing') {
                statusClass = 'processing';
                statusText = 'Processing';
            }

            item.innerHTML = `
                <div class="data-info">
                    <i class="fas fa-video"></i>
                    <span class="data-name" title="${video.filename}">${video.filename}</span>
                </div>
                <span class="data-status ${statusClass}">${statusText}</span>
            `;
            
            // Click to select video for filtering
            item.addEventListener('click', () => {
                // Toggle selection
                if (selectedVideoId === video.id) {
                    selectedVideoId = null;
                    item.classList.remove('selected');
                    addLog(`Cleared video filter.`);
                    updateSearchPlaceholder(null);
                } else {
                    selectedVideoId = video.id;
                    // Remove selected class from others
                    document.querySelectorAll('.data-item').forEach(el => el.classList.remove('selected'));
                    item.classList.add('selected');
                    addLog(`Selected video for search: ${video.filename}`);
                    updateSearchPlaceholder(video.filename);
                }
            });

            dataList.appendChild(item);
        });
    }
    
    // Update search input placeholder based on video selection
    function updateSearchPlaceholder(filename) {
        const searchInput = document.getElementById('searchInput');
        if (!searchInput) return;
        
        if (filename) {
            searchInput.placeholder = `Search in "${filename.substring(0, 30)}${filename.length > 30 ? '...' : ''}"`;
            searchInput.classList.add('filtered');
        } else {
            searchInput.placeholder = "Describe the scene you're looking for...";
            searchInput.classList.remove('filtered');
        }
    }

    if (dataTabs) {
        dataTabs.forEach(tab => {
            tab.addEventListener('click', () => {
                dataTabs.forEach(t => t.classList.remove('active'));
                tab.classList.add('active');
                const view = tab.getAttribute('data-view');
                // Reset to first page when switching tabs
                fetchIngestedData(view, true);
            });
        });
    }

    // Initial Load
    fetchIngestedData();

    // Poll for updates every 5 seconds
    setInterval(() => {
        // Only poll if we are on the 'all' or 'recent' view to avoid jitter if user is interacting
        // For simplicity, we just refresh. A more robust solution would check if user is dragging/selecting.
        const activeTab = document.querySelector('.data-tab.active');
        const view = activeTab ? activeTab.getAttribute('data-view') : 'all';
        fetchIngestedData(view);
    }, 5000);

    // --- Drag & Drop Upload Handling ---
    if (dropZone) {
        dropZone.addEventListener('click', () => fileInput.click());

        dropZone.addEventListener('dragover', (e) => {
            e.preventDefault();
            dropZone.classList.add('dragover');
        });

        dropZone.addEventListener('dragleave', () => {
            dropZone.classList.remove('dragover');
        });

        dropZone.addEventListener('drop', (e) => {
            e.preventDefault();
            dropZone.classList.remove('dragover');
            const files = e.dataTransfer.files;
            if (files.length > 0) {
                handleFileUpload(files[0]);
            }
        });
    }

    if (fileInput) {
        fileInput.addEventListener('change', (e) => {
            if (e.target.files.length > 0) {
                handleFileUpload(e.target.files[0]);
            }
        });
    }

    async function handleFileUpload(file) {
        const formData = new FormData();
        formData.append('file', file);
        
        // Get Config from State
        const config = configurations[currentConfigId];
        formData.append('config', JSON.stringify(config));

        statusText.textContent = `Uploading ${file.name}...`;
        addLog(`Starting upload: ${file.name} (${(file.size / 1024 / 1024).toFixed(2)} MB)`);
        addLog(`Using Pipeline: ${config.name}`);
        addLog(`Configuration: ${JSON.stringify(config)}`);

        try {
            const response = await fetch('/upload', {
                method: 'POST',
                body: formData
            });

            if (response.ok) {
                const result = await response.json();
                statusText.textContent = 'Upload complete!';
                statusText.style.color = 'var(--success)';
                addLog(`Upload successful: ${result.filename}`);
                addLog('Video queued for processing. Check the status in the sidebar.');
                
                // Refresh video list to show new upload
                setTimeout(() => fetchIngestedData(), 1000);
            } else {
                const err = await response.text();
                throw new Error(err);
            }
        } catch (error) {
            console.error('Upload error:', error);
            statusText.textContent = 'Upload failed.';
            statusText.style.color = 'var(--error)';
            addLog(`Error uploading file: ${error.message}`);
        } finally {
            fileInput.value = ''; 
        }
    }

    // --- Chat / Search Handling ---
    let selectedVideoId = null; // Global state for selected video

    function appendMessage(sender, content, isHtml = false) {
        const msgDiv = document.createElement('div');
        msgDiv.className = `message ${sender}-message`;
        
        const contentDiv = document.createElement('div');
        contentDiv.className = 'message-content';
        
        if (isHtml) {
            contentDiv.innerHTML = content;
        } else {
            contentDiv.textContent = content;
        }
        
        msgDiv.appendChild(contentDiv);
        chatHistory.appendChild(msgDiv);
        chatHistory.scrollTop = chatHistory.scrollHeight;
    }

    async function handleSearch() {
        const query = searchInput.value.trim();
        if (!query) return;

        appendMessage('user', query);
        searchInput.value = '';
        
        const loadingId = 'loading-' + Date.now();
        const loadingDiv = document.createElement('div');
        loadingDiv.className = 'message system-message';
        loadingDiv.id = loadingId;
        loadingDiv.innerHTML = '<div class="message-content">Searching video archives...</div>';
        chatHistory.appendChild(loadingDiv);
        chatHistory.scrollTop = chatHistory.scrollHeight;

        addLog(`Processing query: "${query}"`);
        if (selectedVideoId) {
            addLog(`Filtering by video ID: ${selectedVideoId}`);
        }

        try {
            let url = `/search?q=${encodeURIComponent(query)}`;
            if (selectedVideoId) {
                url += `&video_id=${selectedVideoId}`;
            }

            // Add config parameters (vision, speech, frame_interval only - embedding is fixed)
            const config = configurations[currentConfigId];
            if (config) {
                url += `&vision_model=${encodeURIComponent(config.vision_model)}`;
                url += `&speech_model=${encodeURIComponent(config.speech_model)}`;
                url += `&frame_interval=${config.frame_interval || 5}`;
            }
            
            console.log(`[Search] URL: ${url}`);
            addLog(`Searching with config: ${config?.name || 'default'}`);
            
            const response = await fetch(url);
            
            // Check if response is OK before parsing
            if (!response.ok) {
                const errorText = await response.text();
                throw new Error(`Server error: ${response.status} - ${errorText}`);
            }
            
            // Log debug headers
            const debugHash = response.headers.get('X-Debug-Config-Hash');
            const debugCollection = response.headers.get('X-Debug-Collection-Name');
            if (debugHash) {
                console.log(`[Search] Backend config_hash: ${debugHash}`);
                console.log(`[Search] Collection: ${debugCollection}`);
            }
            
            const results = await response.json();
            
            const loadingEl = document.getElementById(loadingId);
            if (loadingEl) loadingEl.remove();

            if (results.length === 0) {
                appendMessage('system', 'No relevant video clips found.');
                addLog('Search completed. No results.');
                return;
            }

            addLog(`Found ${results.length} matches.`);

            let resultHtml = `<div class="results-header">Found ${results.length} relevant clip${results.length !== 1 ? 's' : ''}:</div>`;
            
            results.forEach((res, index) => {
                // Determine score badge class
                const scorePct = (res.score || 0) * 100;
                let scoreClass = 'medium';
                if (scorePct >= 80) scoreClass = 'high';
                else if (scorePct < 50) scoreClass = 'low';

                // Safe property access with defaults
                const text = res.text || 'No text available';
                const filename = res.filename || 'Unknown';
                const startTime = res.start || 0;
                const endTime = res.end || 0;
                const videoId = res.video_id || '';
                const resultType = res.type || 'unknown';

                // Escape text for safe HTML insertion
                const escapedText = text.replace(/`/g, "\\`").replace(/\\/g, "\\\\").replace(/'/g, "\\'");
                const escapedFilename = filename.replace(/'/g, "\\'");

                // Highlight keywords in text
                const highlightedText = text.replace(new RegExp(query, 'gi'), match => `<mark class="highlight">${match}</mark>`);

                resultHtml += `
                    <div class="video-result-card" onclick="playClip('${videoId}', ${startTime}, ${endTime}, '${escapedFilename}', \`${escapedText}\`)">
                        <div class="card-content-section">
                            <div class="card-header">
                                <span class="card-filename" title="${filename}">
                                    <i class="fas fa-film"></i> ${filename}
                                </span>
                                <div class="card-badges">
                                    <span class="badge badge-score ${scoreClass}">${scorePct.toFixed(0)}%</span>
                                    <span class="badge badge-type">${resultType}</span>
                                </div>
                            </div>
                            <div class="card-transcript">${highlightedText}</div>
                            <div class="card-footer">
                                <span class="card-timestamp">
                                    <i class="fas fa-clock"></i>
                                    ${formatTimeDisplay(startTime)} - ${formatTimeDisplay(endTime)}
                                </span>
                                <span class="card-play-hint">
                                    <i class="fas fa-play-circle"></i> Click to play
                                </span>
                            </div>
                        </div>
                        <div class="card-thumbnail">
                            <img 
                                src="/thumbnail/${videoId}?t=${startTime}" 
                                alt="Video thumbnail at ${formatTimeDisplay(startTime)}"
                                loading="lazy"
                                onerror="this.parentElement.innerHTML='<div class=\\'thumbnail-placeholder\\'><i class=\\'fas fa-video\\'></i><span>${formatTimeDisplay(startTime)}</span></div>'"
                            />
                            <div class="thumbnail-timestamp">${formatTimeDisplay(startTime)}</div>
                            <div class="play-overlay">
                                <i class="fas fa-play-circle"></i>
                            </div>
                        </div>
                    </div>
                `;
            });

            appendMessage('system', resultHtml, true);

        } catch (error) {
            console.error('Search error:', error);
            const loadingEl = document.getElementById(loadingId);
            if (loadingEl) loadingEl.remove();
            
            let errorMsg = 'Sorry, an error occurred while searching.';
            if (error.message.includes('Server error')) {
                errorMsg += ' The server returned an error. Please check if the video has been fully indexed.';
            } else if (error.message.includes('collection')) {
                errorMsg += ' The search index may not be ready yet. Please wait for the video to be fully indexed.';
            }
            
            appendMessage('system', errorMsg);
            addLog(`Search error: ${error.message}`);
        }
    }

    if (sendBtn) {
        sendBtn.addEventListener('click', handleSearch);
    }

    if (searchInput) {
        searchInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                handleSearch();
            }
        });
    }

    // --- Modal Handling ---
    const modal = document.getElementById("settingsModal");
    const closeModalBtn = document.querySelector(".close-modal-btn");
    const saveBtn = document.getElementById("saveConfigBtn");
    const cancelBtn = document.getElementById("cancelConfigBtn");

    // Helper to handle card selection
    function setupCardSelection(containerId) {
        const container = document.getElementById(containerId);
        if (!container) return;
        
        const cards = container.querySelectorAll('.option-card');
        cards.forEach(card => {
            card.addEventListener('click', () => {
                // Deselect all in this container
                cards.forEach(c => c.classList.remove('selected'));
                // Select clicked
                card.classList.add('selected');
            });
        });
    }

    // Initialize card listeners
    setupCardSelection('visionOptions');
    setupCardSelection('speechOptions');

    // Toggle Vision Section Logic
    const visionCheck = document.getElementById('enableVisionCheck');
    const visionSection = document.getElementById('visionSectionContainer');

    if (visionCheck && visionSection) {
        visionCheck.addEventListener('change', (e) => {
            if (e.target.checked) {
                visionSection.classList.remove('dimmed');
            } else {
                visionSection.classList.add('dimmed');
            }
        });
    }

    function openSettingsModal() {
        if (!modal) return;
        
        const config = configurations[currentConfigId];
        
        // Helper to select card
        const selectCard = (containerId, value) => {
            const container = document.getElementById(containerId);
            if (!container) return;
            
            // Clear previous selection
            container.querySelectorAll('.option-card').forEach(c => c.classList.remove('selected'));
            
            // Select new
            const card = container.querySelector(`.option-card[data-value="${value}"]`);
            if (card) card.classList.add('selected');
        };

        selectCard('visionOptions', config.vision_model);
        selectCard('speechOptions', config.speech_model);

        // Set Enable Vision Checkbox & Dimming State
        if (visionCheck) {
            visionCheck.checked = config.enable_vision;
            // Trigger change event to update UI state
            visionCheck.dispatchEvent(new Event('change'));
        }

        modal.style.display = "flex";
    }

    function saveConfiguration() {
        const config = configurations[currentConfigId];
        if (!config) return;

        // Helper to get selected value
        const getSelectedValue = (containerId) => {
            const container = document.getElementById(containerId);
            if (!container) return null;
            const card = container.querySelector('.option-card.selected');
            return card ? card.getAttribute('data-value') : null;
        };

        const visionVal = getSelectedValue('visionOptions');
        if (visionVal) config.vision_model = visionVal;

        const speechVal = getSelectedValue('speechOptions');
        if (speechVal) config.speech_model = speechVal;

        // Get Enable Vision Checkbox
        const visionCheck = document.getElementById('enableVisionCheck');
        if (visionCheck) config.enable_vision = visionCheck.checked;

        // Persist to backend
        fetch(`/configs/${currentConfigId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(config)
        })
        .then(response => {
            if (response.ok) {
                return response.json();
            }
            throw new Error('Failed to save configuration');
        })
        .then(savedConfig => {
            configurations[currentConfigId] = savedConfig;
            updateConfigSummary();
            modal.style.display = "none";
            addLog(`Configuration '${config.name}' saved to server.`);
            
            // Refresh data list to reflect any changes
            fetchIngestedData();
        })
        .catch(error => {
            console.error('Error saving configuration:', error);
            // Still update locally even if server save fails
            updateConfigSummary();
            modal.style.display = "none";
            addLog(`Configuration updated locally (server save failed: ${error.message})`);
        });
    }

    if (closeModalBtn) {
        closeModalBtn.onclick = function() {
            modal.style.display = "none";
        }
    }

    if (cancelBtn) {
        cancelBtn.onclick = function() {
            modal.style.display = "none";
        }
    }

    if (saveBtn) {
        saveBtn.onclick = function() {
            saveConfiguration();
        }
    }

    window.onclick = function(event) {
        if (event.target == modal) {
            modal.style.display = "none";
        }
        const videoModal = document.getElementById('videoPlayerModal');
        if (event.target == videoModal) {
            closeVideoPlayerModal();
        }
    }

    // --- Video Player Modal ---
    const videoPlayerModal = document.getElementById('videoPlayerModal');
    const videoPlayer = document.getElementById('videoPlayer');
    const closeVideoPlayerBtn = document.getElementById('closeVideoPlayer');
    const replayClipBtn = document.getElementById('replayClipBtn');
    const playFullVideoBtn = document.getElementById('playFullVideoBtn');
    const clipTimeRange = document.getElementById('clipTimeRange');
    const matchedTextContent = document.getElementById('matchedTextContent');
    const videoPlayerTitle = document.getElementById('videoPlayerTitle');
    const videoPlayerSubtitle = document.getElementById('videoPlayerSubtitle');

    let currentClipStart = 0;
    let currentClipEnd = 0;
    let clipEndCheckInterval = null;
    let isClosingPlayer = false;  // Flag to prevent error on intentional close

    window.playClip = function(videoId, startTime, endTime, filename, matchedText) {
        if (!videoId) {
            addLog('Error: No video ID provided');
            return;
        }

        isClosingPlayer = false;  // Reset flag when opening
        currentClipStart = startTime;
        currentClipEnd = endTime;

        // Update modal UI
        videoPlayerTitle.textContent = filename;
        videoPlayerSubtitle.textContent = `Clip: ${formatTime(startTime)} - ${formatTime(endTime)}`;
        clipTimeRange.innerHTML = `<i class="fas fa-clock"></i> ${formatTime(startTime)} - ${formatTime(endTime)}`;
        matchedTextContent.textContent = matchedText;

        // Set video source with streaming endpoint
        const streamUrl = `/stream/${videoId}`;
        videoPlayer.src = streamUrl;
        
        // Show modal
        videoPlayerModal.style.display = 'flex';
        
        // When video is ready, seek to start time
        videoPlayer.onloadedmetadata = function() {
            videoPlayer.currentTime = startTime;
            videoPlayer.play();
            addLog(`Playing clip from ${formatTime(startTime)} to ${formatTime(endTime)}`);
            
            // Start monitoring to pause at clip end
            startClipEndMonitor(endTime);
        };

        videoPlayer.onerror = function() {
            // Don't show error if we're intentionally closing the player
            if (isClosingPlayer) return;
            addLog(`Error loading video: ${videoId}`);
            alert('Error loading video. Please try again.');
        };
    };

    function startClipEndMonitor(endTime) {
        // Clear any existing interval
        if (clipEndCheckInterval) {
            clearInterval(clipEndCheckInterval);
        }
        
        // Check every 100ms if we've reached the clip end
        clipEndCheckInterval = setInterval(() => {
            if (videoPlayer.currentTime >= endTime) {
                videoPlayer.pause();
                clearInterval(clipEndCheckInterval);
                clipEndCheckInterval = null;
                addLog('Clip playback completed');
            }
        }, 100);
    }

    function closeVideoPlayerModal() {
        isClosingPlayer = true;  // Set flag before clearing source
        videoPlayerModal.style.display = 'none';
        videoPlayer.pause();
        videoPlayer.removeAttribute('src');  // Better than setting to empty string
        videoPlayer.load();  // Reset the video element
        if (clipEndCheckInterval) {
            clearInterval(clipEndCheckInterval);
            clipEndCheckInterval = null;
        }
    }

    if (closeVideoPlayerBtn) {
        closeVideoPlayerBtn.onclick = closeVideoPlayerModal;
    }

    if (replayClipBtn) {
        replayClipBtn.onclick = function() {
            videoPlayer.currentTime = currentClipStart;
            videoPlayer.play();
            startClipEndMonitor(currentClipEnd);
            addLog(`Replaying clip from ${formatTime(currentClipStart)}`);
        };
    }

    if (playFullVideoBtn) {
        playFullVideoBtn.onclick = function() {
            // Clear clip end monitoring - let user watch full video
            if (clipEndCheckInterval) {
                clearInterval(clipEndCheckInterval);
                clipEndCheckInterval = null;
            }
            videoPlayer.currentTime = 0;
            videoPlayer.play();
            videoPlayerSubtitle.textContent = 'Playing full video';
            addLog('Playing full video');
        };
    }

    function formatTime(seconds) {
        const mins = Math.floor(seconds / 60);
        const secs = Math.floor(seconds % 60);
        return `${mins}:${secs.toString().padStart(2, '0')}`;
    }

    // Helper for display with hours if needed
    function formatTimeDisplay(seconds) {
        const hrs = Math.floor(seconds / 3600);
        const mins = Math.floor((seconds % 3600) / 60);
        const secs = Math.floor(seconds % 60);
        
        if (hrs > 0) {
            return `${hrs}:${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
        }
        return `${mins}:${secs.toString().padStart(2, '0')}`;
    }
    
    // Make formatTimeDisplay available globally for search results
    window.formatTimeDisplay = formatTimeDisplay;

});
