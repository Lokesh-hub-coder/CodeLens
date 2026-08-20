package com.codelensai.parser;

import com.codelensai.parser.model.CodeEntities;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;

import java.io.IOException;
import java.nio.file.Path;
import java.nio.file.Paths;

/**
 * CLI entry point: java -jar parser-service-1.0.0.jar <repo-root> <output.json>
 *
 * Invoked once per index request by backend/app/parsing/java_bridge.py via
 * subprocess. Exits 0 on success, non-zero on any error so java_bridge.py can
 * distinguish a clean run from a failure without parsing stderr.
 */
public final class ParserApplication {

    private ParserApplication() {}

    public static void main(String[] args) throws IOException {
        if (args.length != 2) {
            System.err.println("Usage: parser-service-1.0.0.jar <repo-root> <output-json>");
            System.exit(1);
        }

        Path repoRoot   = Paths.get(args[0]);
        Path outputPath = Paths.get(args[1]);

        if (!repoRoot.toFile().isDirectory()) {
            System.err.println("ERROR: repo-root does not exist or is not a directory: " + repoRoot);
            System.exit(1);
        }

        RepoParser parser = new RepoParser(repoRoot);
        CodeEntities.ParseResult result = parser.parse();

        ObjectMapper mapper = new ObjectMapper()
                .enable(SerializationFeature.INDENT_OUTPUT);
        mapper.writeValue(outputPath.toFile(), result);

        // Summary to stdout so java_bridge.py can log it if desired
        System.out.println("Parsed: " + result.classes.size() + " classes, "
                + result.methods.size() + " methods, "
                + result.call_edges.size() + " call edges, "
                + result.import_edges.size() + " import edges, "
                + result.unparsed_files.size() + " unparsed files");
    }
}
