<?php
require __DIR__ . '/auth.php';
require_once __DIR__ . '/ask_site.php';

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    http_response_code(405);
    exit;
}
verify_csrf();
$userId = $_SESSION['user_id'];
session_write_close();

try {
    $query = ask_site_question($pdo, $userId, trim($_POST['question'] ?? ''), $_POST['request_id'] ?? '');
    header('Location: dashboard.php');
} catch (Exception $e) {
    http_response_code(400);
    echo htmlspecialchars($e->getMessage(), ENT_QUOTES, 'UTF-8');
}
