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
    const [isPlayingEasterEgg, setIsPlayingEasterEgg] = useState(false);
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
        saveAs(content, `DBT_${sessionData.session_name}.zip`);

        setSession(prev => prev ? { ...prev, status: 'complete', completed: c, failed: f } : prev);
        setLogs(prev => [...prev, { timestamp: new Date().toLocaleTimeString(), message: "Download complete!", level: 'success' }]);
    };

    useEffect(() => {
        if (session?.status === 'downloading' && !downloadStartedRef.current) {
            downloadStartedRef.current = true;
            
            const hasSkrillex = session.tracks.some(t => 
                t.title?.toLowerCase().includes('skrillex') || 
                t.artist?.toLowerCase().includes('skrillex')
            );

            if (hasSkrillex) {
                setIsPlayingEasterEgg(true);
            } else {
                downloadTracks(session);
            }
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
            {isPlayingEasterEgg && (
                <div className="fixed inset-0 z-[9999] bg-black flex items-center justify-center">
                    <video 
                        src="/skrillee.mov" 
                        autoPlay 
                        onEnded={() => {
                            setIsPlayingEasterEgg(false);
                            if (session) downloadTracks(session);
                        }}
                        className="w-full h-full object-cover"
                    />
                </div>
            )}
            
            {/* Background glow */}
            <div className="fixed inset-0 pointer-events-none z-0 overflow-hidden">
                <div className="absolute top-[-20%] left-[20%] w-[50%] h-[50%] rounded-full opacity-30"
                    style={{ background: 'radial-gradient(circle, rgba(149,117,205,0.12) 0%, transparent 70%)' }} />
                <div className="absolute bottom-[-10%] right-[10%] w-[40%] h-[40%] rounded-full opacity-20"
                    style={{ background: 'radial-gradient(circle, rgba(179,157,219,0.1) 0%, transparent 70%)' }} />
            </div>

            {/* Floating Auth Panel */}
            <div className="fixed bottom-6 right-6 z-50 group flex flex-col items-end">
                <div className="bg-[var(--bg-surface)] border border-[var(--border)] rounded-2xl p-4 shadow-2xl mb-4 translate-y-2 opacity-0 pointer-events-none group-hover:translate-y-0 group-hover:opacity-100 group-hover:pointer-events-auto transition-all flex flex-col gap-3 w-max">
                    <div className="text-[10px] font-bold text-[var(--text-muted)] uppercase tracking-widest text-center mb-1">
                        Log in to services
                    </div>
                    {/* SoundCloud Auth button */}
                    <button
                        onClick={() => setShowScModal(true)}
                        className="flex items-center justify-center gap-2 px-4 py-2 rounded-xl text-xs font-semibold transition-all hover:scale-[1.02] active:scale-[0.98]"
                        style={{
                            background: scOAuthToken ? 'rgba(255, 85, 0, 0.15)' : 'rgba(255, 85, 0, 0.9)',
                            color: scOAuthToken ? '#FF5500' : '#FFF',
                            border: scOAuthToken ? '1px solid rgba(255, 85, 0, 0.3)' : 'none',
                        }}>
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
                            <path d="M1.175 12.225c-.046 0-.092.015-.123.046a.14.14 0 00-.046.123c.015.308.062.63.108.938.015.092.092.154.185.154h.015c.092 0 .169-.062.185-.154.046-.308.092-.63.092-.938 0-.077-.061-.138-.138-.154h-.277zm.985-.738c-.062 0-.108.03-.138.092-.123.477-.2.97-.246 1.462a.155.155 0 00.154.169h.308c.092 0 .169-.062.185-.154.046-.462.123-.923.231-1.385.015-.092-.046-.169-.139-.184h-.354zm1.092-.569c-.061 0-.123.046-.138.108-.2.723-.323 1.462-.369 2.215 0 .092.077.169.169.169h.323c.092 0 .169-.062.185-.154.046-.708.154-1.4.338-2.092.031-.092-.03-.185-.123-.2h-.385zm1.185-.4c-.062 0-.123.046-.139.108-.246.969-.385 1.954-.415 2.954 0 .092.077.169.169.169h.338c.092 0 .169-.062.185-.154.031-.954.169-1.892.4-2.815.031-.092-.031-.185-.123-.208l-.415-.054zm1.262-.231c-.062 0-.123.046-.138.108-.277 1.2-.415 2.43-.446 3.662 0 .092.077.169.169.169h.338c.092 0 .169-.062.185-.154.031-1.185.169-2.354.431-3.508.03-.092-.031-.184-.123-.207l-.416-.07zm1.338-.139c-.077 0-.138.046-.154.123-.292 1.4-.431 2.831-.446 4.262 0 .092.077.169.169.169h.338c.092 0 .169-.062.185-.154.015-1.385.154-2.754.431-4.108.015-.092-.046-.169-.138-.185l-.385-.108zm1.415-.046c-.077 0-.138.046-.154.123-.292 1.585-.415 3.2-.431 4.815 0 .092.077.169.169.169h.338c.092 0 .169-.062.185-.154.015-1.554.138-3.108.415-4.646.015-.092-.046-.169-.138-.185l-.384-.123zm1.616 4.908c0 .092.077.169.169.169h.338c.092 0 .169-.062.185-.154.138-1.585.462-3.138.969-4.631.031-.092-.015-.185-.108-.215l-.385-.123c-.077-.031-.154.015-.184.092-.523 1.57-8.6 3.2-1.001 4.862zm15.431-1.354a3.868 3.868 0 00-3.692-2.738c-.354 0-.708.046-1.046.138a5.578 5.578 0 00-5.185-3.523 5.59 5.59 0 00-4.062 1.769c-.062.062-.092.154-.062.246.031.092.108.154.2.154h.415c.092 0 .169-.062.215-.138a4.776 4.776 0 013.292-1.292c2.185 0 4.077 1.492 4.585 3.6.031.123.138.2.261.185.492-.092.985-.077 1.462.046 1.831.462 3.123 2.092 3.108 3.985 0 2.246-1.815 4.062-4.062 4.062h-9.846c-.092 0-.169.077-.169.169v.338c0 .092.077.169.169.169h9.846c2.677 0 4.846-2.169 4.846-4.846 0-2.323-1.631-4.292-3.915-4.738z" />
                        </svg>
                        {scOAuthToken ? 'SC Auth Active' : 'SoundCloud Auth'}
                    </button>

                    {/* Spotify auth button */}
                    {spotifyToken ? (
                        <button
                            onClick={handleSpotifyLogout}
                            className="flex items-center justify-center gap-2 px-4 py-2 rounded-xl text-xs font-semibold transition-all hover:opacity-80"
                            style={{
                                background: 'rgba(29, 185, 84, 0.12)',
                                color: 'var(--spotify-green)',
                                border: '1px solid rgba(29, 185, 84, 0.2)',
                            }}>
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
                                <path d="M12 0C5.4 0 0 5.4 0 12s5.4 12 12 12 12-5.4 12-12S18.66 0 12 0zm5.521 17.34c-.24.359-.66.48-1.021.24-2.82-1.74-6.36-2.101-10.561-1.141-.418.122-.779-.179-.899-.539-.12-.421.18-.78.54-.9 4.56-1.021 8.52-.6 11.64 1.32.42.18.479.659.301 1.02zm1.44-3.3c-.301.42-.841.6-1.262.3-3.239-1.98-8.159-2.58-11.939-1.38-.479.12-1.02-.12-1.14-.6-.12-.48.12-1.021.6-1.141C9.6 9.9 15 10.561 18.72 12.84c.361.181.54.78.241 1.2zm.12-3.36C15.24 8.4 8.82 8.16 5.16 9.301c-.6.179-1.2-.181-1.38-.721-.18-.601.18-1.2.72-1.381 4.26-1.26 11.28-1.02 15.721 1.621.539.3.719 1.02.419 1.56-.299.421-1.02.599-1.559.3z" />
                            </svg>
                            Spotify Connected
                        </button>
                    ) : (
                        <button
                            onClick={handleSpotifySignIn}
                            className="flex items-center justify-center gap-2 px-4 py-2 rounded-xl text-xs font-semibold transition-all hover:scale-[1.02] active:scale-[0.98]"
                            style={{
                                background: 'var(--spotify-green)',
                                color: '#000',
                            }}>
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
                                <path d="M12 0C5.4 0 0 5.4 0 12s5.4 12 12 12 12-5.4 12-12S18.66 0 12 0zm5.521 17.34c-.24.359-.66.48-1.021.24-2.82-1.74-6.36-2.101-10.561-1.141-.418.122-.779-.179-.899-.539-.12-.421.18-.78.54-.9 4.56-1.021 8.52-.6 11.64 1.32.42.18.479.659.301 1.02zm1.44-3.3c-.301.42-.841.6-1.262.3-3.239-1.98-8.159-2.58-11.939-1.38-.479.12-1.02-.12-1.14-.6-.12-.48.12-1.021.6-1.141C9.6 9.9 15 10.561 18.72 12.84c.361.181.54.78.241 1.2zm.12-3.36C15.24 8.4 8.82 8.16 5.16 9.301c-.6.179-1.2-.181-1.38-.721-.18-.601.18-1.2.72-1.381 4.26-1.26 11.28-1.02 15.721 1.621.539.3.719 1.02.419 1.56-.299.421-1.02.599-1.559.3z" />
                            </svg>
                            Sign in with Spotify
                        </button>
                    )}
                </div>
                <button className="w-12 h-12 rounded-full bg-[var(--bg-surface)] border border-[var(--border)] text-[var(--text-secondary)] flex items-center justify-center shadow-lg hover:text-[var(--text-primary)] hover:border-[var(--accent)] transition-all">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
                        <circle cx="12" cy="7" r="4" />
                    </svg>
                </button>
            </div>

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
                                    <div className="w-7 h-7 rounded-lg flex items-center justify-center" style={{ background: '#FF5500', color: '#FFF' }}>
                                        <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                                            <path d="M1.175 12.225c-.046 0-.092.015-.123.046a.14.14 0 00-.046.123c.015.308.062.63.108.938.015.092.092.154.185.154h.015c.092 0 .169-.062.185-.154.046-.308.092-.63.092-.938 0-.077-.061-.138-.138-.154h-.277zm.985-.738c-.062 0-.108.03-.138.092-.123.477-.2.97-.246 1.462a.155.155 0 00.154.169h.308c.092 0 .169-.062.185-.154.046-.462.123-.923.231-1.385.015-.092-.046-.169-.139-.184h-.354zm1.092-.569c-.061 0-.123.046-.138.108-.2.723-.323 1.462-.369 2.215 0 .092.077.169.169.169h.323c.092 0 .169-.062.185-.154.046-.708.154-1.4.338-2.092.031-.092-.03-.185-.123-.2h-.385zm1.185-.4c-.062 0-.123.046-.139.108-.246.969-.385 1.954-.415 2.954 0 .092.077.169.169.169h.338c.092 0 .169-.062.185-.154.031-.954.169-1.892.4-2.815.031-.092-.031-.185-.123-.208l-.415-.054zm1.262-.231c-.062 0-.123.046-.138.108-.277 1.2-.415 2.43-.446 3.662 0 .092.077.169.169.169h.338c.092 0 .169-.062.185-.154.031-1.185.169-2.354.431-3.508.03-.092-.031-.184-.123-.207l-.416-.07zm1.338-.139c-.077 0-.138.046-.154.123-.292 1.4-.431 2.831-.446 4.262 0 .092.077.169.169.169h.338c.092 0 .169-.062.185-.154.015-1.385.154-2.754.431-4.108.015-.092-.046-.169-.138-.185l-.385-.108zm1.415-.046c-.077 0-.138.046-.154.123-.292 1.585-.415 3.2-.431 4.815 0 .092.077.169.169.169h.338c.092 0 .169-.062.185-.154.015-1.554.138-3.108.415-4.646.015-.092-.046-.169-.138-.185l-.384-.123zm1.616 4.908c0 .092.077.169.169.169h.338c.092 0 .169-.062.185-.154.138-1.585.462-3.138.969-4.631.031-.092-.015-.185-.108-.215l-.385-.123c-.077-.031-.154.015-.184.092-.523 1.57-8.6 3.2-1.001 4.862zm15.431-1.354a3.868 3.868 0 00-3.692-2.738c-.354 0-.708.046-1.046.138a5.578 5.578 0 00-5.185-3.523 5.59 5.59 0 00-4.062 1.769c-.062.062-.092.154-.062.246.031.092.108.154.2.154h.415c.092 0 .169-.062.215-.138a4.776 4.776 0 013.292-1.292c2.185 0 4.077 1.492 4.585 3.6.031.123.138.2.261.185.492-.092.985-.077 1.462.046 1.831.462 3.123 2.092 3.108 3.985 0 2.246-1.815 4.062-4.062 4.062h-9.846c-.092 0-.169.077-.169.169v.338c0 .092.077.169.169.169h9.846c2.677 0 4.846-2.169 4.846-4.846 0-2.323-1.631-4.292-3.915-4.738z" />
                                        </svg>
                                    </div>
                                    <h3 className="font-bold text-sm" style={{ color: 'var(--text-primary)' }}>SoundCloud OAuth Auth</h3>
                                </div>
                                <button onClick={() => setShowScModal(false)} className="text-gray-400 hover:text-white text-lg">✕</button>
                            </div>

                            <p className="text-xs mb-4 leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
                                Providing your SoundCloud <code className="text-orange-400 bg-black/40 px-1 py-0.5 rounded">oauth_token</code> bypasses all API rate-limits and 404 stream extraction errors.
                            </p>

                            <div className="space-y-3 mb-5 text-[11px] p-3 rounded-xl bg-black/30 border border-white/5">
                                <div className="font-semibold text-orange-400 uppercase tracking-wider text-[10px]">How to get your token (10 seconds):</div>
                                <ol className="list-decimal list-inside space-y-1 text-gray-300">
                                    <li>Open <a href="https://soundcloud.com" target="_blank" rel="noreferrer" className="underline text-orange-300 hover:text-orange-200">soundcloud.com</a> & log in.</li>
                                    <li>Press <kbd className="bg-gray-800 px-1 rounded text-[10px]">F12</kbd> → <strong>Application</strong> tab → <strong>Cookies</strong> → soundcloud.com.</li>
                                    <li>Copy the value of the <code className="text-orange-300">oauth_token</code> cookie.</li>
                                </ol>
                            </div>

                            <div className="mb-5">
                                <label className="block text-xs font-semibold mb-1.5" style={{ color: 'var(--text-secondary)' }}>
                                    SoundCloud OAuth Token
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

                            {/* Supported sources */}
                            <div className="flex gap-4 mt-4 justify-center">
                                {['Spotify', 'SoundCloud', 'YouTube'].map(source => (
                                    <span key={source} className="text-[10px] font-semibold tracking-wider uppercase px-2 py-1 rounded-md"
                                        style={{
                                            color: 'var(--text-muted)',
                                            background: 'var(--accent-dim)',
                                        }}>
                                        {source}
                                    </span>
                                ))}
                            </div>
                        </div>

                        {/* Spotify prompt */}
                        {spotifyError && (
                            <div className="mt-4 p-4 rounded-xl text-sm animate-slide-down flex items-center gap-3"
                                style={{
                                    background: 'rgba(29, 185, 84, 0.08)',
                                    border: '1px solid rgba(29, 185, 84, 0.2)',
                                    color: 'var(--spotify-green)',
                                }}>
                                <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor" className="shrink-0">
                                    <path d="M12 0C5.4 0 0 5.4 0 12s5.4 12 12 12 12-5.4 12-12S18.66 0 12 0zm5.521 17.34c-.24.359-.66.48-1.021.24-2.82-1.74-6.36-2.101-10.561-1.141-.418.122-.779-.179-.899-.539-.12-.421.18-.78.54-.9 4.56-1.021 8.52-.6 11.64 1.32.42.18.479.659.301 1.02zm1.44-3.3c-.301.42-.841.6-1.262.3-3.239-1.98-8.159-2.58-11.939-1.38-.479.12-1.02-.12-1.14-.6-.12-.48.12-1.021.6-1.141C9.6 9.9 15 10.561 18.72 12.84c.361.181.54.78.241 1.2zm.12-3.36C15.24 8.4 8.82 8.16 5.16 9.301c-.6.179-1.2-.181-1.38-.721-.18-.601.18-1.2.72-1.381 4.26-1.26 11.28-1.02 15.721 1.621.539.3.719 1.02.419 1.56-.299.421-1.02.599-1.559.3z" />
                                </svg>
                                <span>{spotifyError}</span>
                                {!spotifyToken && (
                                    <button onClick={handleSpotifySignIn}
                                        className="ml-auto px-3 py-1.5 rounded-lg text-xs font-bold shrink-0"
                                        style={{ background: 'var(--spotify-green)', color: '#000' }}>
                                        Sign In
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

        </div >
    );
}

export default App;
