<?php
// Scores Gaitcha behavioral payloads (JSON array on stdin) with Gaitcha's own
// parser and scorer. Prints a JSON array of results in the same order.
declare(strict_types=1);

require '/opt/gaitcha/src/php/BehavioralLogParser.php';
require '/opt/gaitcha/src/php/BehavioralScorer.php';

$payloads = json_decode((string) file_get_contents('php://stdin'), true, 512, JSON_THROW_ON_ERROR);
$parser = new Gaitcha\BehavioralLogParser();
$scorer = new Gaitcha\BehavioralScorer();
$results = [];
foreach ($payloads as $payload) {
    $parsed = $parser->parse(json_encode($payload, JSON_THROW_ON_ERROR));
    if (!$parsed['valid']) {
        $results[] = ['score' => 0.0, 'profile' => 'invalid', 'details' => ['reason' => $parsed['reason']]];
        continue;
    }
    $results[] = $scorer->score($parsed['data']);
}
echo json_encode($results, JSON_THROW_ON_ERROR);
