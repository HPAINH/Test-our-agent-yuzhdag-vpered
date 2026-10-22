import { useEffect, useState } from "react";
import { api, getToken, setToken, User } from "./api";
import { go, replace, useRoute } from "./router";
import Login from "./pages/Login";
import Home from "./pages/Home";
import Feed from "./pages/Feed";
import InsightPage from "./pages/InsightPage";
import Analyze from "./pages/Analyze";
import Profile from "./pages/Profile";
import Quality from "./pages/Quality";
import Embed from "./pages/Embed";
import ErrorBoundary from "./components/ErrorBoundary";

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const route = useRoute();

  const launchToken = route.name === "launch" ? route.token : null;

  useEffect(() => {
    if (route.name === "embed" || launchToken !== null) return;
    if (!getToken()) {
      setReady(true);
      return;
    }
    api
      .me()
      .then(setUser)
      .catch(() => setToken(null))
      .finally(() => setReady(true));
  }, []);

  useEffect(() => {
    if (launchToken === null) return;
    setReady(false);
    api
      .launch(launchToken)
      .then((r) => {
        setToken(r.token);
        setUser(r.user);
        setNotice(null);
        replace(`/${r.target}`);
      })
      .catch((e) => {
        setToken(null);
        setUser(null);
        setNotice(e.message);
        replace("/home");
      })
      .finally(() => setReady(true));
  }, [launchToken]);

  if (route.name === "embed") return <Embed token={route.token} />;
  if (!ready) return <div className="spinner">Загрузка…</div>;
  if (!user)
    return (
      <Login
        notice={notice}
        onLogin={(u) => {
          setNotice(null);
          setUser(u);
          go("/home");
        }}
      />
    );

  const switchUser = async (login: string) => {
    const r = await api.login(login);
    setToken(r.token);
    setUser(r.user);
    go("/feed");
  };

  let page;
  switch (route.name) {
    case "feed":
      page = <Feed key={user.login} />;
      break;
    case "insight":
      page = <InsightPage key={route.id + user.login} insightId={route.id} />;
      break;
    case "event":
      page = <InsightPage key={route.id + user.login} eventId={route.id} />;
      break;
    case "analyze":
      page = <Analyze key={user.login} user={user} />;
      break;
    case "profile":
      page = <Profile key={user.login} />;
      break;
    case "quality":
      page = user.is_admin ? <Quality /> : <Home key={user.login} user={user} />;
      break;
    default:
      page = <Home key={user.login} user={user} />;
  }

  return (
    <Shell user={user} route={route.name} onSwitch={switchUser} onLogout={() => { setToken(null); setUser(null); }}>
      {page}
    </Shell>
  );
}

function Shell({
  user, route, children, onSwitch, onLogout,
}: {
  user: User;
  route: string;
  children: React.ReactNode;
  onSwitch: (login: string) => void;
  onLogout: () => void;
}) {
  const [menu, setMenu] = useState(false);
  const [users, setUsers] = useState<User[]>([]);
  const [badge, setBadge] = useState<number | null>(null);

  useEffect(() => {
    api.widget().then((w) => setBadge(w.new_count)).catch(() => setBadge(null));
  }, [user.login]);

  const openMenu = () => {
    setMenu((m) => !m);
    if (!users.length) api.users().then(setUsers).catch(() => undefined);
  };

  const nav = [
    { key: "home", label: "Главная", path: "/home" },
    { key: "accounts", label: "Счета и выписки", disabled: true },
    { key: "payments", label: "Платежи", disabled: true },
    { key: "credit", label: "Кредиты и гарантии", disabled: true },
    { key: "feed", label: "Пульс бизнеса", path: "/feed", badge },
    { key: "analyze", label: "Проверить новость", path: "/analyze" },
    { key: "profile", label: "Профиль компании", path: "/profile" },
    ...(user.is_admin ? [{ key: "quality", label: "Качество сервиса", path: "/quality" }] : []),
  ];
  const active = route === "insight" || route === "event" ? "feed" : route;

  return (
    <div className="shell">
      <aside className="side" aria-label="Разделы интернет-банка">
        <div className="brand">
          <b>Бизнес</b>
          <span>интернет-банк</span>
        </div>
        {nav.map((n) =>
          n.disabled ? (
            <span key={n.key} className="nav-item disabled" title="В прототипе раздел не реализован">
              {n.label}
            </span>
          ) : (
            <a key={n.key} href={`#${n.path}`} className={`nav-item ${active === n.key ? "active" : ""}`}>
              <span>{n.label}</span>
              {n.badge ? <span className="nav-badge" aria-label={`${n.badge} новых событий`}>{n.badge}</span> : null}
            </a>
          ),
        )}
        <div className="side-foot">Прототип команды «Эльбрус». Данные синтетические.</div>
      </aside>
      <div className="main">
        <header className="topbar">
          <div className="company-chip">
            <b>{companyName(user)}</b>
            <span className="small muted">
              {user.company.industry} · {user.company.region}
            </span>
          </div>
          <div className="user-switch">
            <button className="btn ghost" onClick={openMenu} aria-expanded={menu}>
              {user.display_name}, {user.role_name.toLowerCase()}
            </button>
            {menu && (
              <div className="user-menu" role="menu">
                <div className="small muted" style={{ padding: "6px 10px" }}>
                  Войти как другой пользователь
                </div>
                {users.map((u) => (
                  <button key={u.login} role="menuitem" onClick={() => { setMenu(false); onSwitch(u.login); }}>
                    <div>
                      {u.display_name} — {u.role_name}
                    </div>
                    <div className="small muted">{companyName(u)}</div>
                  </button>
                ))}
                <button role="menuitem" onClick={onLogout}>
                  Выйти
                </button>
              </div>
            )}
          </div>
        </header>
        <main className="content">
          <ErrorBoundary resetKey={route}>{children}</ErrorBoundary>
        </main>
      </div>
    </div>
  );
}

export function companyName(u: User) {
  const names: Record<string, string> = {
    U_DEMO_A: "Металлургический комбинат «А» (демо)",
    U_DEMO_B: "Продуктовая сеть «Б» (демо)",
    U_DEMO_C: "Девелопер «В» (демо)",
  };
  return names[u.company.id] || `Компания ${u.company.id}`;
}
