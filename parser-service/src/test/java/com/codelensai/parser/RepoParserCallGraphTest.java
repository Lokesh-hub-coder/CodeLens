package com.codelensai.parser;

import com.codelensai.parser.model.CodeEntities;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.net.URISyntaxException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Set;
import java.util.stream.Collectors;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class RepoParserCallGraphTest {

    @Test
    void resolvesLabelledInRepositoryCallsWithMeasuredPrecisionAndRecall() throws Exception {
        Path fixture = fixtureRoot();
        Set<String> expected = Files.readAllLines(
                        fixture.resolve("expected-call-edges.txt"))
                .stream()
                .filter(line -> !line.isBlank() && !line.startsWith("#"))
                .collect(Collectors.toSet());

        CodeEntities.ParseResult parsed = new RepoParser(fixture).parse();
        Set<String> actual = parsed.call_edges.stream()
                .map(edge -> edge.caller_id + " -> " + edge.callee_id)
                .collect(Collectors.toSet());

        Set<String> truePositives = actual.stream()
                .filter(expected::contains)
                .collect(Collectors.toSet());
        double precision = truePositives.size() / (double) actual.size();
        double recall = truePositives.size() / (double) expected.size();

        assertTrue(parsed.unparsed_files.isEmpty(), parsed.unparsed_files.toString());
        assertEquals(expected, actual);
        assertEquals(1.0, precision);
        assertEquals(1.0, recall);
    }

    @Test
    void backendCanExcludeTestSourcesBeforeParsing(@TempDir Path repo) throws Exception {
        Path mainSource = repo.resolve("src/main/java/sample/Main.java");
        Path testSource = repo.resolve("src/test/java/sample/MainTest.java");
        Files.createDirectories(mainSource.getParent());
        Files.createDirectories(testSource.getParent());
        Files.writeString(
                mainSource,
                "package sample; public class Main { public void run() {} }");
        Files.writeString(
                testSource,
                "package sample; public class MainTest { public void test() { new Main().run(); } }");

        CodeEntities.ParseResult parsed = new RepoParser(repo, List.of(), true).parse();

        assertEquals(1, parsed.classes.size());
        assertEquals(1, parsed.methods.size());
        assertTrue(parsed.call_edges.isEmpty());
        assertTrue(parsed.unparsed_files.isEmpty());
    }

    @Test
    void unresolvedNestedTypesKeepTheirQualifiedOwnerName(@TempDir Path repo) throws Exception {
        Path source = repo.resolve("src/main/java/sample/Outer.java");
        Files.createDirectories(source.getParent());
        Files.writeString(
                source,
                "package sample; public class Outer { "
                        + "static class Inner { void run() {} } }");

        CodeEntities.ParseResult parsed = new RepoParser(repo).parse();

        assertTrue(parsed.classes.stream().anyMatch(
                type -> type.id.equals("sample.Outer.Inner")));
        assertTrue(parsed.methods.stream().anyMatch(
                method -> method.id.equals("sample.Outer.Inner#run()")));
    }

    private static Path fixtureRoot() throws URISyntaxException {
        return Path.of(RepoParserCallGraphTest.class
                        .getResource("/call-graph-fixture")
                        .toURI())
                .toAbsolutePath();
    }
}
