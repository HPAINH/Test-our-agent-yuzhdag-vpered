import { useEffect, useState } from "react";
import { api, setToken, User } from "../api";
import { companyName } from "../App";

export default function Login({ onLogin, notice }: { onLogin: (u: User) => void; notice?: string | null }) {
  const [users, setUsers] = useState<User[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    api.users().then(setUsers).catch((e) => setError(e.message));
  }, []);

  const login = async (login: string) => {
    setBusy(login);
    try {
      const r = await api.login(login);
      setToken(r.token);
      onLogin(r.user);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="login">
      <div className="login-art">
        <div className="brand" style={{ padding: 0 }}>
          <b>Бизнес</b>
          <span style={{ color: "#c3c8d4" }}>интернет-банк</span>
        </div>
        <div>
          <h1>Новости, которые меняют решения вашей компании</h1>
          <p>
            Пульс бизнеса отбирает события под профиль компании и роль руководителя, объясняет последствия
            и показывает позицию аналитиков банка. Каждый вывод ведёт к первоисточнику.
          </p>
        </div>
        <p className="small" style={{ color: "#8e95a6" }}>Прототип команды «Эльбрус», Альфа-Будущее Хакатон 2026</p>
      </div>
      <div className="login-panel">
        <h2>Вход в демо-кабинет</h2>
        <p className="muted">Выберите тестового пользователя. Компании и данные синтетические.</p>
        {notice && <div className="error">{notice}</div>}
        {error && <div className="error">{error}</div>}
        <div className="login-list">
          {users.map((u) => (
            <button key={u.login} className="login-row" onClick={() => login(u.login)} disabled={!!busy}>
              <div>
                <div>
                  <b>{u.display_name}</b> — {u.role_name}
                </div>
                <div className="small muted">
                  {companyName(u)} · {u.company.industry}
                </div>
              </div>
              <span className="small muted">{busy === u.login ? "Вход…" : "Войти"}</span>
            </button>
          ))}
          {!users.length && !error && (
            <div className="spinner">
              Загружаем данные кейса: 85 тыс. компаний и 1,4 млн платежей. Первый запуск занимает несколько минут —
              обновите страницу чуть позже.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
