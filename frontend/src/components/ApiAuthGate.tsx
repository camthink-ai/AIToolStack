import { useEffect, useState } from 'react';
import { AUTH_REQUIRED_EVENT, getApiKey, setApiKey } from '../apiAuth';
import './ApiAuthGate.css';

/**
 * Modal shown when the backend rejects a request with 401/auth_required.
 * Prompts for the API access key, stores it and reloads so every pending
 * request is retried with the key attached.
 */
export function ApiAuthGate() {
  const [visible, setVisible] = useState(false);
  const [value, setValue] = useState('');

  useEffect(() => {
    const handler = () => {
      setValue((getApiKey() || ''));
      setVisible(true);
    };
    window.addEventListener(AUTH_REQUIRED_EVENT, handler);
    return () => window.removeEventListener(AUTH_REQUIRED_EVENT, handler);
  }, []);

  if (!visible) return null;

  const submit = () => {
    setApiKey(value);
    window.location.reload();
  };

  return (
    <div className="api-auth-overlay" role="dialog" aria-modal="true">
      <div className="api-auth-modal">
        <h2>访问密钥 / Access Key Required</h2>
        <p>
          此服务器已启用 API 认证，请输入访问密钥以继续。
          <br />
          This server requires an API access key. Paste your key to continue.
        </p>
        <input
          type="password"
          autoFocus
          value={value}
          placeholder="API Key"
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') submit();
          }}
        />
        <div className="api-auth-actions">
          <button className="api-auth-primary" onClick={submit}>
            保存并刷新 / Save &amp; Reload
          </button>
        </div>
        <p className="api-auth-hint">
          密钥由服务器管理员在部署时配置（API_KEY 环境变量）。
          <br />
          The key is configured by the server administrator (API_KEY env variable).
        </p>
      </div>
    </div>
  );
}
