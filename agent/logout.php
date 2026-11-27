<?php
require_once __DIR__ . '/session.php';

// Проверяем CSRF-токен (защита от случайного/намеренного разлогинивания)
$token = $_GET['token'] ?? '';
if (!hash_equals($_SESSION['csrf_token'] ?? '', $token)) {
    http_response_code(403);
    die('Недействительный токен');
}

// Уничтожаем сессию
$_SESSION = [];

if (ini_get('session.use_cookies')) {
    $params = session_get_cookie_params();
    setcookie(
        session_name(),
        '',
        time() - 42000,
        $params['path'],
        $params['domain'],
        $params['secure'],
        $params['httponly']
    );
}

session_destroy();

header('Location: login.php');
exit;