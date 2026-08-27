import { useState, useEffect, useCallback, useRef } from 'react';
import useWebSocket from 'react-use-websocket';
import JSZip from 'jszip';
import { saveAs } from 'file-saver';

// Types
interface TrackInfo {
    title: string;
    artist: string;
}

interface TrackStatus {
    status: 'pending' | 'downloading' | 'complete' | 'error';
    progress: number;
    speed: string;
    error?: string;
}

interface LogEntry {
    timestamp: string;
    message: string;
    level: 'info' | 'warning' | 'error' | 'success';
}

interface SessionState {
    session_id: string;
    session_name: string;
    total: number;
    completed: number;
    failed: number;
    tracks: TrackInfo[];
    trackStatuses: TrackStatus[];
    status: 'resolving' | 'downloading' | 'zipping' | 'complete' | 'error';
    errors: { track: string; error: string }[];
    zip_filename?: string;
}

const API_BASE = '/api';

function App() {
    const [url, setUrl] = useState('');
    const [isSubmitting, setIsSubmitting] = useState(false);
    const [session, setSession] = useState<SessionState | null>(null);
    const [logs, setLogs] = useState<LogEntry[]>([]);
    const [showConsole, setShowConsole] = useState(true);
    const [spotifyToken, setSpotifyToken] = useState<string | null>(
        () => localStorage.getItem('spotify_access_token')
    );
    const [scOAuthToken, setScOAuthToken] = useState<string>(
        () => localStorage.getItem('sc_oauth_token') || ''
    );
    const [showScModal, setShowScModal] = useState(false);
    const [spotifyError, setSpotifyError] = useState('');
    const [globalError, setGlobalError] = useState('');
    const [allowLongTracks, setAllowLongTracks] = useState(false);
    const inputRef = useRef<HTMLInputElement>(null);
    const consoleEndRef = useRef<HTMLDivElement>(null);
    const downloadStartedRef = useRef(false);

    // Auto-scroll console to bottom
    useEffect(() => {
        if (showConsole) {
            consoleEndRef.current?.scrollIntoView({ behavior: 'smooth' });
        }
    }, [logs, showConsole]);

    // WebSocket
    const clientIdRef = useRef(Math.random().toString(36).substring(2, 15));
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/${clientIdRef.current}`;

    const { lastMessage } = useWebSocket(wsUrl, {
        shouldReconnect: () => true,
        share: true,
    });

    // Handle OAuth callback on page load
    useEffect(() => {
        const params = new URLSearchParams(window.location.search);
        const code = params.get('code');
        if (code) {
            fetch(`${API_BASE}/spotify/callback`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ code }),
            })
                .then(res => res.json())
                .then(data => {
                    if (data.access_token) {
                        localStorage.setItem('spotify_access_token', data.access_token);
                        localStorage.setItem('spotify_refresh_token', data.refresh_token || '');
                        setSpotifyToken(data.access_token);
                        setSpotifyError('');
                    }
                })
                .catch(err => {
                    console.error('OAuth callback error:', err);
                    setSpotifyError('Failed to sign in with Spotify');
                })
                .finally(() => {
                    window.history.replaceState({}, '', '/');
                });
        }
    }, []);

    // Handle WebSocket messages
    useEffect(() => {
        if (!lastMessage) return;
        try {
            const data = JSON.parse(lastMessage.data);
            handleWsMessage(data);
        } catch (e) {
            console.error('WS parse error:', e);
        }
    }, [lastMessage]);

    const handleWsMessage = useCallback((data: any) => {
        switch (data.type) {
            case 'log':
                setLogs(prev => [...prev, {
                    timestamp: data.timestamp || new Date().toLocaleTimeString(),
                    message: data.message,
                    level: data.level || 'info',
                }]);
                break;

            case 'session_start':
                setSession({
                    session_id: data.session_id,
                    session_name: data.session_name || 'Resolving URL...',
                    total: data.total || 0,
                    completed: 0,
                    failed: 0,
                    tracks: data.tracks || [],
                    trackStatuses: (data.tracks || []).map(() => ({
                        status: 'pending' as const,
                        progress: 0,
                        speed: '',
                    })),
                    status: data.status || 'resolving',
                    errors: [],
                });
                setIsSubmitting(false);
                setGlobalError('');
                break;

            case 'session_resolved':
                setSession(prev => prev ? {
                    ...prev,
                    session_name: data.session_name,
                    total: data.total,
                    tracks: data.tracks || [],
                    trackStatuses: (data.tracks || []).map(() => ({
                        status: 'pending' as const,
                        progress: 0,
                        speed: '',
                    })),
                    status: 'downloading',
                } : prev);
                break;

            case 'track_start':
                setSession(prev => {
                    if (!prev) return prev;
                    const statuses = [...prev.trackStatuses];
                    if (statuses[data.track_index]) {
                        statuses[data.track_index] = {
                            ...statuses[data.track_index],
                            status: 'downloading',
                            progress: 0,
                        };
                    }
                    return { ...prev, trackStatuses: statuses };
                });
                break;

            case 'track_progress':
                setSession(prev => {
                    if (!prev) return prev;
                    const statuses = [...prev.trackStatuses];
                    if (statuses[data.track_index]) {
                        statuses[data.track_index] = {
                            ...statuses[data.track_index],
                            progress: data.progress,
                            speed: data.speed || '',
                            status: 'downloading',
                        };
                    }
                    return { ...prev, trackStatuses: statuses };
                });
                break;

            case 'track_complete':
                setSession(prev => {
                    if (!prev) return prev;
                    const statuses = [...prev.trackStatuses];
                    if (statuses[data.track_index]) {
                        statuses[data.track_index] = {
                            ...statuses[data.track_index],
                            status: 'complete',
                            progress: 100,
                        };
                    }
                    return {
                        ...prev,
                        completed: data.completed,
                        trackStatuses: statuses,
                    };
                });
                break;

            case 'track_error':
                setSession(prev => {
                    if (!prev) return prev;
                    const statuses = [...prev.trackStatuses];
                    if (statuses[data.track_index]) {
                        statuses[data.track_index] = {
                            ...statuses[data.track_index],
                            status: 'error',
                            error: data.error,
                        };
                    }
                    return {
                        ...prev,
                        failed: prev.failed + 1,
                        trackStatuses: statuses,
                        errors: [...prev.errors, { track: data.track_title, error: data.error }],
                    };
                });
                break;

            case 'session_zipping':
                setSession(prev => prev ? { ...prev, status: 'zipping' } : prev);
                break;

            case 'session_complete':
                setSession(prev => prev ? {
                    ...prev,
                    status: 'complete',
                    completed: data.completed,
                    failed: data.failed,
                    errors: data.errors || [],
                    zip_filename: data.zip_filename,
                } : prev);
                break;

            case 'session_error':
                if (data.error && (data.error.includes('403') || data.error.includes('401') || data.error.toLowerCase().includes('token') || data.error.toLowerCase().includes('expired'))) {
                    const rt = localStorage.getItem('spotify_refresh_token');
                    if (rt) {
                        fetch(`${API_BASE}/spotify/refresh`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ refresh_token: rt })
                        }).then(res => {
                            if (!res.ok) throw new Error('Refresh failed');
                            return res.json();
                        }).then(refreshData => {
                            if (refreshData.access_token) {
                                localStorage.setItem('spotify_access_token', refreshData.access_token);
                                localStorage.setItem('spotify_refresh_token', refreshData.refresh_token || rt);
                                setSpotifyToken(refreshData.access_token);
                                setGlobalError('Spotify session refreshed! Please try downloading again.');
                            } else {
                                throw new Error('Invalid refresh payload');
                            }
                        }).catch(() => {
                            handleSpotifyLogout();
                            setSpotifyError('Your Spotify session expired. Please sign in again.');
                        });
                    } else {
                        handleSpotifyLogout();
                        setSpotifyError('Your Spotify session expired. Please sign in again.');
                    }
                }

                setSession(prev => {
                    if (prev) {
                        return { ...prev, status: 'error', errors: [{ track: '', error: data.error }] };
                    }
                    return prev;
                });
                setIsSubmitting(false);
                setGlobalError(data.error);
                break;
        }
    }, []);



    // Handle download
    const handleDownload = async () => {
        if (!url.trim()) return;
        setIsSubmitting(true);
        setGlobalError('');
        setSpotifyError('');
        setLogs([]);

        const isSpotify = url.includes('spotify.com/');

        if (isSpotify && !spotifyToken) {
            setSpotifyError('Please sign in with Spotify first to download Spotify links.');
            setIsSubmitting(false);
            return;
        }

        setLogs([]);
        setSession(null);
        downloadStartedRef.current = false;

        try {
            const res = await fetch(`${API_BASE}/download`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    url: url.trim(),
                    spotify_token: spotifyToken,
                    sc_oauth_token: scOAuthToken || null,
                    allow_long_tracks: allowLongTracks,
                    client_id: clientIdRef.current
                })
            });

            const data = await res.json();
            if (!res.ok) {
                if (data.detail && data.detail.includes("Spotify")) {
                    setSpotifyError(data.detail);
                } else {
                    setGlobalError(data.detail || 'Failed to start download');
                }
                setIsSubmitting(false);
            }
        } catch (e) {
            setGlobalError('Connection error to backend API');
            setIsSubmitting(false);
        }
    };

    // Spotify Sign In
    const handleSpotifySignIn = async () => {
        try {
            const res = await fetch(`${API_BASE}/spotify/auth-url`);
            const data = await res.json();
            if (data.auth_url) {
                window.location.href = data.auth_url;
            }
        } catch (e) {
            setSpotifyError('Failed to connect to Spotify');
        }
    };

    // Spotify Logout
    const handleSpotifyLogout = () => {
        localStorage.removeItem('spotify_access_token');
        localStorage.removeItem('spotify_refresh_token');
        setSpotifyToken(null);
    };

    // Trigger client-side JSZip download loop
    const downloadTracks = async (sessionData: SessionState) => {
        const zip = new JSZip();
        const tracksFolder = zip.folder(sessionData.session_name) || zip;
        let c = 0;
        let f = 0;

        const concurrencyLimit = 3;
        let currentIndex = 0;

        const downloadWorker = async () => {
            while (currentIndex < sessionData.tracks.length) {
                const index = currentIndex++;
                const track = sessionData.tracks[index];
                try {
                    const res = await fetch(`${API_BASE}/extract_track`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            session_id: sessionData.session_id,
                            track_index: index,
                            sc_oauth_token: localStorage.getItem('sc_oauth_token') || null
                        })
                    });

                    if (!res.ok) throw new Error("Failed to extract");
                    
                    const blob = await res.blob();
                    const filename = `${track.artist} - ${track.title}.mp3`.replace(/[/\\?%*:|"<>]/g, '-');
                    tracksFolder.file(filename, blob);
                    c++;
                } catch (e) {
                    f++;
                }
            }
        };

        const workers = Array(Math.min(concurrencyLimit, sessionData.tracks.length))
            .fill(null)
            .map(() => downloadWorker());

        await Promise.all(workers);

        setSession(prev => prev ? { ...prev, status: 'zipping' } : prev);
        setLogs(prev => [...prev, { timestamp: new Date().toLocaleTimeString(), message: "Generating ZIP file locally in browser...", level: 'info' }]);
        
        const content = await zip.generateAsync({ type: 'blob' });
        saveAs(content, `Music_${sessionData.session_name}.zip`);

        setSession(prev => prev ? { ...prev, status: 'complete', completed: c, failed: f } : prev);
        setLogs(prev => [...prev, { timestamp: new Date().toLocaleTimeString(), message: "Download complete!", level: 'success' }]);
    };

    useEffect(() => {
        if (session?.status === 'downloading' && !downloadStartedRef.current) {
            downloadStartedRef.current = true;
            downloadTracks(session);
        }
    }, [session?.status]);

    // Reset
    const handleNewDownload = () => {
        setSession(null);
        setLogs([]);
        setGlobalError('');
        setSpotifyError('');
        downloadStartedRef.current = false;
        setTimeout(() => inputRef.current?.focus(), 100);
    };

    const overallProgress = session
        ? session.total > 0
            ? Math.round(((session.completed + session.failed) / session.total) * 100)
            : 0
        : 0;

    const isActive = session && (session.status === 'resolving' || session.status === 'downloading' || session.status === 'zipping');

    return (
        <div className="min-h-screen flex flex-col" style={{ background: 'var(--bg-primary)' }}>
            {/* Background glow */}
            <div className="fixed inset-0 pointer-events-none z-0 overflow-hidden">
                <div className="absolute top-[-20%] left-[20%] w-[50%] h-[50%] rounded-full opacity-30"
                    style={{ background: 'radial-gradient(circle, rgba(149,117,205,0.12) 0%, transparent 70%)' }} />
                <div className="absolute bottom-[-10%] right-[10%] w-[40%] h-[40%] rounded-full opacity-20"
                    style={{ background: 'radial-gradient(circle, rgba(179,157,219,0.1) 0%, transparent 70%)' }} />
            </div>

            {/* Header */}
            <header className="relative z-10 flex items-center justify-between px-6 py-4"
                style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                <div className="flex items-center gap-3">
                    <div className="w-9 h-9 rounded-xl flex items-center justify-center text-lg font-black"
                        style={{
                            background: 'linear-gradient(135deg, var(--accent), var(--accent-hover))',
                            color: 'var(--bg-primary)',
                            boxShadow: '0 4px 15px var(--accent-glow)',
                        }}>
                        A
                    </div>
                    <div>
                        <h1 className="text-lg font-bold tracking-tight" style={{ color: 'var(--text-primary)' }}>
                            Audio Downloader
                        </h1>
                        <p className="text-[10px] font-medium tracking-widest uppercase" style={{ color: 'var(--text-muted)' }}>
                            High Quality Music Downloads
                        </p>
                    </div>
                </div>

                {/* Header Auth buttons */}
                <div className="flex items-center gap-3">
                    {/* Advanced Auth button */}
                    <button
                        onClick={() => setShowScModal(true)}
                        className="flex items-center gap-2 px-3.5 py-1.5 rounded-full text-xs font-semibold transition-all hover:scale-[1.02] active:scale-[0.98]"
                        style={{
                            background: scOAuthToken ? 'rgba(var(--accent-rgb), 0.15)' : 'var(--accent)',
                            color: scOAuthToken ? 'var(--accent)' : 'var(--bg-primary)',
                            border: scOAuthToken ? '1px solid rgba(var(--accent-rgb), 0.3)' : 'none',
                        }}>
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                            <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
                            <path d="M7 11V7a5 5 0 0 1 10 0v4" />
                        </svg>
                        {scOAuthToken ? 'Auth Active' : 'Advanced Auth'}
                    </button>

                    {/* Platform auth button */}
                    {spotifyToken ? (
                        <button
                            onClick={handleSpotifyLogout}
                            className="flex items-center gap-2 px-3.5 py-1.5 rounded-full text-xs font-semibold transition-all hover:opacity-80"
                            style={{
                                background: 'rgba(var(--accent-rgb), 0.12)',
                                color: 'var(--accent)',
                                border: '1px solid rgba(var(--accent-rgb), 0.2)',
                            }}>
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                <polyline points="20 6 9 17 4 12" />
                            </svg>
                            Platform Connected
                        </button>
                    ) : (
                        <button
                            onClick={handleSpotifySignIn}
                            className="flex items-center gap-2 px-3.5 py-1.5 rounded-full text-xs font-semibold transition-all hover:scale-[1.02] active:scale-[0.98]"
                            style={{
                                background: 'transparent',
                                color: 'var(--text-secondary)',
                                border: '1px solid var(--border)',
                            }}>
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
                            </svg>
                            Connect Platform
                        </button>
                    )}
                </div>
            </header>

            {/* SoundCloud Auth Modal */}
            {
                showScModal && (
                    <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
                        style={{ background: 'rgba(0,0,0,0.7)', backdropFilter: 'blur(4px)' }}>
                        <div className="w-full max-w-md rounded-2xl p-6 shadow-2xl animate-fade-in"
                            style={{
                                background: 'var(--bg-surface)',
                                border: '1px solid var(--border)',
                            }}>
                            <div className="flex items-center justify-between mb-4">
                                <div className="flex items-center gap-2">
                                    <div className="w-7 h-7 rounded-lg flex items-center justify-center" style={{ background: 'var(--accent)', color: 'var(--bg-primary)' }}>
                                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                            <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
                                            <path d="M7 11V7a5 5 0 0 1 10 0v4" />
                                        </svg>
                                    </div>
                                    <h3 className="font-bold text-sm" style={{ color: 'var(--text-primary)' }}>Advanced Authentication</h3>
                                </div>
                                <button onClick={() => setShowScModal(false)} className="text-gray-400 hover:text-white text-lg">✕</button>
                            </div>

                            <p className="text-xs mb-4 leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
                                Providing your platform <code className="text-[var(--accent)] bg-black/40 px-1 py-0.5 rounded">oauth_token</code> bypasses API rate-limits and stream extraction errors.
                            </p>

                            <div className="space-y-3 mb-5 text-[11px] p-3 rounded-xl bg-black/30 border border-white/5">
                                <div className="font-semibold uppercase tracking-wider text-[10px]" style={{ color: 'var(--accent)' }}>How to get your token (10 seconds):</div>
                                <ol className="list-decimal list-inside space-y-1 text-gray-300">
                                    <li>Open the <span className="underline" style={{ color: 'var(--accent)' }}>target platform</span> & log in.</li>
                                    <li>Press <kbd className="bg-gray-800 px-1 rounded text-[10px]">F12</kbd> → <strong>Application</strong> tab → <strong>Cookies</strong>.</li>
                                    <li>Copy the value of the <code style={{ color: 'var(--accent)' }}>oauth_token</code> cookie.</li>
                                </ol>
                            </div>

                            <div className="mb-5">
                                <label className="block text-xs font-semibold mb-1.5" style={{ color: 'var(--text-secondary)' }}>
                                    Platform OAuth Token
                                </label>
                                <input
                                    type="text"
                                    value={scOAuthToken}
                                    onChange={(e) => {
                                        const val = e.target.value.trim();
                                        setScOAuthToken(val);
                                        localStorage.setItem('sc_oauth_token', val);
                                    }}
                                    placeholder="e.g. 2-293847-1029384..."
                                    className="w-full px-3.5 py-2.5 rounded-xl text-xs font-mono outline-none transition-all"
                                    style={{
                                        background: 'var(--bg-elevated)',
                                        border: '1px solid var(--border)',
                                        color: 'var(--text-primary)',
                                    }}
                                />
                            </div>

                            <div className="flex gap-2 justify-end">
                                {scOAuthToken && (
                                    <button
                                        onClick={() => {
                                            setScOAuthToken('');
                                            localStorage.removeItem('sc_oauth_token');
                                        }}
                                        className="px-4 py-2 rounded-xl text-xs font-semibold text-red-400 bg-red-500/10 hover:bg-red-500/20">
                                        Clear Token
                                    </button>
                                )}
                                <button
                                    onClick={() => setShowScModal(false)}
                                    className="px-5 py-2 rounded-xl text-xs font-bold text-black"
                                    style={{ background: '#FF5500' }}>
                                    Save & Close
                                </button>
                            </div>
                        </div>
                    </div>
                )
            }

            {/* Main content */}
            <main className="relative z-10 flex-1 flex flex-col items-center justify-center px-4 py-8">
                {!session ? (
                    /* Input state */
                    <div className="w-full max-w-xl animate-fade-in">
                        <div className="text-center mb-8">
                            <h2 className="text-3xl font-extrabold mb-3 tracking-tight" style={{ color: 'var(--text-primary)' }}>
                                Download Music
                            </h2>
                            <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>
                                Paste a music or playlist link below
                            </p>
                        </div>

                        {/* Main input box */}
                        <div className="rounded-2xl p-6"
                            style={{
                                background: 'var(--bg-surface)',
                                border: '1px solid var(--border)',
                                boxShadow: '0 8px 32px rgba(0,0,0,0.3), 0 0 0 1px var(--border-subtle)',
                            }}>
                            <div className="flex gap-3">
                                <input
                                    ref={inputRef}
                                    id="url-input"
                                    type="text"
                                    placeholder="Paste URL or search query..."
                                    value={url}
                                    onChange={(e) => setUrl(e.target.value)}
                                    onKeyDown={(e) => e.key === 'Enter' && handleDownload()}
                                    disabled={isSubmitting}
                                    autoFocus
                                    className="flex-1 px-4 py-3 rounded-xl text-sm font-medium outline-none transition-all placeholder:font-normal"
                                    style={{
                                        background: 'var(--bg-primary)',
                                        border: '1px solid var(--border)',
                                        color: 'var(--text-primary)',
                                    }}
                                    onFocus={(e) => (e.target.style.borderColor = 'var(--accent)')}
                                    onBlur={(e) => (e.target.style.borderColor = 'var(--border)')}
                                />
                                <button
                                    id="download-btn"
                                    onClick={handleDownload}
                                    disabled={isSubmitting || !url.trim()}
                                    className="px-6 py-3 rounded-xl text-sm font-bold transition-all disabled:opacity-40 disabled:cursor-not-allowed hover:scale-[1.02] active:scale-[0.98]"
                                    style={{
                                        background: 'linear-gradient(135deg, var(--accent), var(--accent-hover))',
                                        color: 'var(--bg-primary)',
                                        boxShadow: url.trim() ? '0 4px 20px var(--accent-glow)' : 'none',
                                    }}>
                                    {isSubmitting ? (
                                        <span className="flex items-center gap-2">
                                            <svg className="animate-spin w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3">
                                                <circle cx="12" cy="12" r="10" strokeOpacity="0.3" />
                                                <path d="M12 2a10 10 0 0 1 10 10" strokeLinecap="round" />
                                            </svg>
                                            Starting...
                                        </span>
                                    ) : (
                                        <span className="flex items-center gap-2">
                                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                                                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                                                <polyline points="7 10 12 15 17 10" />
                                                <line x1="12" y1="15" x2="12" y2="3" />
                                            </svg>
                                            Download
                                        </span>
                                    )}
                                </button>
                            </div>
                            
                            <div className="mt-4 flex items-center justify-between">
                                <label className="flex items-center gap-2 text-sm text-gray-400 hover:text-white cursor-pointer transition-colors">
                                    <input 
                                        type="checkbox" 
                                        checked={allowLongTracks}
                                        onChange={(e) => setAllowLongTracks(e.target.checked)}
                                        className="rounded border-gray-600 bg-gray-700 text-orange-500 focus:ring-orange-500/50"
                                    />
                                    <span>Allow tracks over 7 minutes</span>
                                </label>
                            </div>

                        </div>

                        {/* Platform prompt */}
                        {spotifyError && (
                            <div className="mt-4 p-4 rounded-xl text-sm animate-slide-down flex items-center gap-3"
                                style={{
                                    background: 'var(--bg-elevated)',
                                    border: '1px solid var(--accent)',
                                    color: 'var(--text-primary)',
                                }}>
                                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0 text-orange-500">
                                    <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
                                </svg>
                                <span>{spotifyError}</span>
                                {!spotifyToken && (
                                    <button onClick={handleSpotifySignIn}
                                        className="ml-auto px-3 py-1.5 rounded-lg text-xs font-bold shrink-0"
                                        style={{ background: 'var(--accent)', color: 'var(--bg-primary)' }}>
                                        Authenticate
                                    </button>
                                )}
                            </div>
                        )}

                        {/* Global error */}
                        {globalError && (
                            <div className="mt-4 p-4 rounded-xl text-sm animate-slide-down"
                                style={{
                                    background: 'rgba(239, 83, 80, 0.08)',
                                    border: '1px solid rgba(239, 83, 80, 0.2)',
                                    color: 'var(--error)',
                                }}>
                                {globalError}
                            </div>
                        )}
                    </div>
                ) : (
                    /* Progress & Active Session State */
                    <div className="w-full max-w-2xl animate-fade-in">
                        {/* Session header */}
                        <div className="text-center mb-6">
                            <h2 className="text-xl font-bold mb-1" style={{ color: 'var(--text-primary)' }}>
                                {session.session_name}
                            </h2>
                            <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>
                                {session.status === 'resolving' && 'Analyzing link and fetching track list...'}
                                {session.status === 'downloading' && `Downloading ${session.completed + session.failed}/${session.total} tracks...`}
                                {session.status === 'zipping' && 'Packaging files into ZIP...'}
                                {session.status === 'complete' && `Done — ${session.completed} of ${session.total} downloaded`}
                                {session.status === 'error' && 'Download failed'}
                            </p>
                        </div>

                        {/* Overall progress bar */}
                        <div className="mb-6 rounded-2xl p-5"
                            style={{
                                background: 'var(--bg-surface)',
                                border: '1px solid var(--border)',
                            }}>
                            <div className="flex justify-between text-xs font-semibold mb-2">
                                <span style={{ color: 'var(--text-secondary)' }}>Overall Progress</span>
                                <span style={{ color: 'var(--accent)' }}>
                                    {session.status === 'resolving' ? 'Resolving...' : `${overallProgress}%`}
                                </span>
                            </div>
                            <div className="h-2.5 rounded-full overflow-hidden" style={{ background: 'var(--bg-primary)' }}>
                                <div
                                    className="h-full rounded-full transition-all duration-500 ease-out"
                                    style={{
                                        width: session.status === 'resolving' ? '15%' : `${session.status === 'zipping' ? 100 : overallProgress}%`,
                                        background: session.status === 'complete'
                                            ? 'var(--success)'
                                            : session.status === 'error'
                                                ? 'var(--error)'
                                                : 'linear-gradient(90deg, var(--accent-hover), var(--accent))',
                                        boxShadow: isActive ? '0 0 12px var(--accent-glow)' : 'none',
                                    }}
                                />
                            </div>
                            {(session.status === 'resolving' || session.status === 'zipping') && (
                                <div className="shimmer h-0.5 rounded-full mt-1" />
                            )}
                        </div>

                        {/* Track list */}
                        {session.tracks.length > 0 && (
                            <div className="rounded-2xl overflow-hidden mb-6"
                                style={{
                                    background: 'var(--bg-surface)',
                                    border: '1px solid var(--border)',
                                }}>
                                <div className="px-5 py-3 text-xs font-semibold uppercase tracking-wider flex justify-between items-center"
                                    style={{
                                        color: 'var(--text-muted)',
                                        borderBottom: '1px solid var(--border-subtle)',
                                        background: 'var(--bg-elevated)',
                                    }}>
                                    <span>Tracks ({session.completed}/{session.total})</span>
                                    <span>Status</span>
                                </div>
                                <div className="max-h-[35vh] overflow-y-auto">
                                    {session.tracks.map((track, i) => {
                                        const st = session.trackStatuses[i];
                                        if (!st) return null;
                                        return (
                                            <div key={i} className="flex items-center gap-3 px-5 py-3 transition-colors"
                                                style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                                                <div className="w-5 h-5 flex items-center justify-center shrink-0">
                                                    {st.status === 'complete' && (
                                                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--success)" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                                                            <polyline points="20 6 9 17 4 12" />
                                                        </svg>
                                                    )}
                                                    {st.status === 'error' && (
                                                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--error)" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                                                            <line x1="18" y1="6" x2="6" y2="18" />
                                                            <line x1="6" y1="6" x2="18" y2="18" />
                                                        </svg>
                                                    )}
                                                    {st.status === 'downloading' && (
                                                        <svg className="animate-spin" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" strokeWidth="3">
                                                            <circle cx="12" cy="12" r="10" strokeOpacity="0.2" />
                                                            <path d="M12 2a10 10 0 0 1 10 10" strokeLinecap="round" />
                                                        </svg>
                                                    )}
                                                    {st.status === 'pending' && (
                                                        <div className="w-2 h-2 rounded-full" style={{ background: 'var(--text-muted)' }} />
                                                    )}
                                                </div>

                                                <div className="flex-1 min-w-0">
                                                    <div className="text-sm font-medium truncate" style={{
                                                        color: st.status === 'error' ? 'var(--error)' : 'var(--text-primary)',
                                                    }}>
                                                        {track.title}
                                                    </div>
                                                    <div className="text-xs truncate" style={{ color: 'var(--text-muted)' }}>
                                                        {track.artist}
                                                        {st.status === 'error' && st.error && ` — ${st.error}`}
                                                    </div>
                                                </div>

                                                {st.status === 'downloading' && (
                                                    <div className="flex items-center gap-2 shrink-0">
                                                        <div className="w-16 h-1.5 rounded-full overflow-hidden" style={{ background: 'var(--bg-primary)' }}>
                                                            <div className="h-full rounded-full transition-all duration-300"
                                                                style={{
                                                                    width: `${st.progress}%`,
                                                                    background: 'var(--accent)',
                                                                }} />
                                                        </div>
                                                        <span className="text-[10px] font-mono w-8 text-right" style={{ color: 'var(--text-muted)' }}>
                                                            {Math.round(st.progress)}%
                                                        </span>
                                                    </div>
                                                )}
                                            </div>
                                        );
                                    })}
                                </div>
                            </div>
                        )}

                        {/* Action buttons */}
                        <div className="flex gap-3 justify-center mb-6">
                            {session.status === 'complete' && (
                                <div className="mt-8 flex justify-center animate-fade-in">
                                    <button
                                        onClick={handleNewDownload}
                                        className="px-8 py-3.5 rounded-2xl font-bold transition-all hover:scale-105 active:scale-95"
                                        style={{
                                            background: 'var(--bg-elevated)',
                                            color: 'var(--text-primary)',
                                            border: '1px solid var(--border)'
                                        }}>
                                        Download Another
                                    </button>
                                </div>
                            )}
                            {session.status === 'complete' && (
                                <button
                                    id="download-zip-btn"
                                    className="px-8 py-3 rounded-xl text-sm font-bold transition-all hover:scale-[1.02] active:scale-[0.98] flex items-center gap-2"
                                    style={{
                                        background: 'linear-gradient(135deg, var(--accent), var(--accent-hover))',
                                        color: 'var(--bg-primary)',
                                        boxShadow: '0 4px 20px var(--accent-glow)',
                                        animation: 'pulse-glow 2s ease-in-out infinite',
                                    }}>
                                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                                        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                                        <polyline points="7 10 12 15 17 10" />
                                        <line x1="12" y1="15" x2="12" y2="3" />
                                    </svg>
                                    Download ZIP
                                </button>
                            )}
                            {(session.status === 'complete' || session.status === 'error') && (
                                <button
                                    id="new-download-btn"
                                    onClick={handleNewDownload}
                                    className="px-6 py-3 rounded-xl text-sm font-semibold transition-all hover:scale-[1.02]"
                                    style={{
                                        background: 'var(--bg-elevated)',
                                        color: 'var(--text-secondary)',
                                        border: '1px solid var(--border)',
                                    }}>
                                    New Download
                                </button>
                            )}
                        </div>

                        {/* Errors summary */}
                        {session.errors.length > 0 && session.status === 'complete' && (
                            <div className="mt-4 rounded-xl p-4 text-xs animate-slide-down"
                                style={{
                                    background: 'rgba(239, 83, 80, 0.06)',
                                    border: '1px solid rgba(239, 83, 80, 0.15)',
                                }}>
                                <div className="font-semibold mb-2" style={{ color: 'var(--error)' }}>
                                    {session.failed} track{session.failed !== 1 ? 's' : ''} failed
                                </div>
                                {session.errors.map((err, i) => (
                                    <div key={i} className="truncate" style={{ color: 'var(--text-muted)' }}>
                                        {err.track}: {err.error}
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>
                )}

                {/* Live Activity Console (Always available) */}
                <div className="w-full max-w-2xl mt-6 rounded-2xl overflow-hidden border transition-all"
                    style={{
                        background: 'var(--bg-surface)',
                        borderColor: 'var(--border)',
                    }}>
                    <button
                        onClick={() => setShowConsole(prev => !prev)}
                        className="w-full px-5 py-3 text-xs font-semibold flex items-center justify-between transition-colors"
                        style={{
                            background: 'var(--bg-elevated)',
                            color: 'var(--text-secondary)',
                            borderBottom: showConsole ? '1px solid var(--border-subtle)' : 'none',
                        }}>
                        <div className="flex items-center gap-2">
                            <span className="w-2 h-2 rounded-full"
                                style={{
                                    background: isActive ? 'var(--accent)' : logs.length > 0 ? 'var(--success)' : 'var(--text-muted)',
                                    boxShadow: isActive ? '0 0 8px var(--accent)' : 'none',
                                }} />
                            <span>Live Activity Console ({logs.length})</span>
                        </div>
                        <span>{showConsole ? 'Hide' : 'Show'}</span>
                    </button>

                    {showConsole && (
                        <div className="p-4 font-mono text-xs max-h-52 overflow-y-auto space-y-1.5"
                            style={{ background: '#0a0810' }}>
                            {logs.length === 0 ? (
                                <div className="text-gray-600 italic">Waiting for activity logs...</div>
                            ) : (
                                logs.map((log, index) => (
                                    <div key={index} className="flex gap-3 leading-relaxed">
                                        <span className="text-gray-600 shrink-0">{log.timestamp}</span>
                                        <span className={`break-all ${log.level === 'error' ? 'text-red-400 font-semibold' :
                                            log.level === 'warning' ? 'text-amber-300' :
                                                log.level === 'success' ? 'text-emerald-400 font-medium' :
                                                    'text-purple-200'
                                            }`}>
                                            {log.message}
                                        </span>
                                    </div>
                                ))
                            )}
                            <div ref={consoleEndRef} />
                        </div>
                    )}
                </div>
            </main>

            {/* Footer */}
            <footer className="relative z-10 text-center py-4">
                <p className="text-[10px] tracking-widest uppercase" style={{ color: 'var(--text-muted)' }}>
                    DBT Downloader • Best Quality MP3
                </p>
            </footer>
        </div >
    );
}

export default App;
