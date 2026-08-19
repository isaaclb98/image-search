<script lang="ts">
  /**
   * Login — single-user. Per backend spec, a single bcrypt
   * password unlocks all routes. POST /api/login sets a cookie
   * via the AuthGateMiddleware. We submit here, then reload /
   * (re-evaluate the session) to navigate on.
   */
  import { goto, invalidateAll } from '$app/navigation';
  import { login } from '$lib/api/client';
  import { toast } from '$lib/components/Toaster.svelte';

  let password = $state('');
  let loading = $state(false);
  let error = $state<string | null>(null);

  async function submit(e: Event) {
    e.preventDefault();
    loading = true;
    error = null;
    try {
      await login(password);
      await invalidateAll();
      goto('/');
    } catch {
      error = 'Wrong password.';
    } finally {
      loading = false;
    }
  }
</script>

<svelte:head>
  <title>Sign in · image-search</title>
</svelte:head>

<section class="wrap">
  <form class="card glass" onsubmit={submit}>
    <h1>image-search</h1>
    <p class="hint">Sign in to search, save, and curate your library.</p>
    <label>
      <span class="lab">Password</span>
      <input
        type="password"
        autocomplete="current-password"
        bind:value={password}
        disabled={loading}
      />
    </label>
    {#if error}<div class="err">{error}</div>{/if}
    <button class="submit" type="submit" disabled={loading || !password}>
      {loading ? 'Signing in…' : 'Sign in'}
    </button>
  </form>
</section>

<style>
  .wrap {
    display: grid;
    place-items: center;
    min-height: 70vh;
  }
  .card {
    width: min(420px, 100%);
    padding: 28px 26px;
    text-align: center;
  }
  h1 {
    margin: 0 0 4px;
    font-size: var(--fs-2xl);
    font-weight: 600;
    letter-spacing: 0.01em;
  }
  .hint {
    color: var(--fg-2);
    margin: 0 0 22px;
    font-size: var(--fs-sm);
  }
  label {
    display: block;
    text-align: left;
    margin-bottom: 12px;
  }
  .lab {
    display: block;
    color: var(--fg-3);
    font-size: var(--fs-sm);
    margin-bottom: 4px;
  }
  input[type='password'] {
    width: 100%;
    height: 44px;
    border-radius: var(--r-pill);
    background: rgba(14,15,20,0.45);
    border: 1px solid var(--glass-edge);
    color: var(--fg-1);
    padding: 0 14px;
  }
  input:focus { border-color: var(--accent); }
  .err {
    color: var(--negative);
    margin: 8px 0 0;
    font-size: var(--fs-sm);
  }
  .submit {
    margin-top: 18px;
    width: 100%;
    height: 44px;
    border-radius: var(--r-pill);
    background: var(--accent);
    color: var(--fg-on-accent);
    font-weight: 600;
  }
  .submit:hover { background: var(--accent-2); }
  .submit:disabled { opacity: 0.5; pointer-events: none; }
</style>
