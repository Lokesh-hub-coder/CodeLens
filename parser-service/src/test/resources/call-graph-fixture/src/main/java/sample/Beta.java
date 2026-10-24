package sample;

public class Beta {
    public void first() {
        new Alpha().target();
    }

    public void second(String value) {
        first();
        value.trim();
    }
}
