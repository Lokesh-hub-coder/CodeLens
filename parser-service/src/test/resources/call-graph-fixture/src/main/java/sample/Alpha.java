package sample;

public class Alpha {
    public void start() {
        Beta beta = new Beta();
        beta.first();
        beta.second("value");
        target();
        System.out.println("external call is not an in-repository edge");
    }

    public void target() {
        new Beta().first();
    }
}
