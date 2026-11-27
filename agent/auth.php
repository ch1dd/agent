<?php

session_set_cookie_params([
    'lifetime' => 3600,      // 1 час
    'path' => '/',
    'secure' => false,       // true только на HTTPS
    'httponly' => true,      // JS не видит cookie
    'samesite' => 'Lax',     // защита от CSRF
]);
session_start();

session_start();
function csrf_token(): string {
    if (empty($_SESSION['csrf_token'])) {
        $_SESSION['csrf_token'] = bin2hex(random_bytes(32));
    }
    return $_SESSION['csrf_token'];
}

function verify_csrf(): void {
    $token = $_POST['csrf_token'] ?? '';
    if (!hash_equals($_SESSION['csrf_token'] ?? '', $token)) {
        http_response_code(403);
        die('Недействительный CSRF-токен');
    }
}

if (!isset($_SESSION['user_id'])) {
    header('Location: login.php');
    exit;
}

function requireAdmin() {
    if (!isset($_SESSION['role']) || $_SESSION['role'] !== 'admin') {
        die('Доступ запрещён');
    }
}